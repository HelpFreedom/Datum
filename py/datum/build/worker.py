"""Сборка детали в отдельном процессе.

Зачем. Скрипт детали — обычный Python: он может зациклиться, съесть всю память
или упасть так, что интерпретатор не восстановить. Пока это идёт в том же
процессе, любая такая беда уносит с собой и командную строку, и (в будущем)
сервер вместе со всеми открытыми проектами.

Как устроено и почему именно так — обе вещи измерены, а не предположены:

  * `fork` при уже загруженном стеке стоит **4.5 мс**. Это дёшево настолько, что
    процесс можно порождать на каждую сборку и не заводить пул. Зато отмена
    получается честной: убиваем процесс целиком, а не уговариваем его.

  * **SIGTERM не прерывает вычисление в C++.** В пробе обработчик сигнала
    отработал лишь через 2.5 с — когда управление вернулось из булевой операции
    manifold3d в Python. Кооперативный флажок «проверяй между операциями» здесь
    бесполезен по той же причине. Поэтому отмена двухступенчатая: SIGTERM даёт
    процессу свернуться по-человечески, а через выдержку идёт SIGKILL, который
    сработает в любом случае.

Результат передаётся не через канал, а файлом: меш бывает в десятки мегабайт, и
канал на таком объёме заклинивает, если никто не читает его одновременно с
записью. Файл пишется атомарно (временное имя плюс переименование), так что
недописанного результата не бывает.
"""

from __future__ import annotations

import json
import os
import pickle
import resource
import signal
import struct
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

__all__ = ["limits_for", "WorkerLimits", "WorkerOutcome", "BuildWorker", "run_in_worker",
           "wait_or_cancel", "current_vsize_mb", "LimitTooLow", "stage", "emit", "emit_to"]


#: Пол для подбираемого предела памяти, МБ.
DEFAULT_MEMORY_MB = 4096


class LimitTooLow(ValueError):
    """Предел памяти ниже того, что уже занимает сам интерпретатор."""


def current_vsize_mb() -> float:
    """Виртуальное адресное пространство текущего процесса, МБ."""
    return vsize_mb_of(os.getpid())


def _proc_mb(pid: int) -> tuple[float, float]:
    """(адресное пространство, занятая память) процесса в МБ; (0, 0) — не прочлось."""
    if sys.platform == "darwin":
        return _libproc_mb(pid)
    return _procfs_vsize_mb(pid), _procfs_rss_mb(pid)


def _procfs_vsize_mb(pid: int) -> float:
    """Linux: `/proc/<pid>/statm`, первое поле — размер в страницах."""
    try:
        if pid == os.getpid():
            with open("/proc/self/status", encoding="ascii") as f:
                for line in f:
                    if line.startswith("VmSize:"):
                        return int(line.split()[1]) / 1024
        with open(f"/proc/{pid}/statm", encoding="ascii") as f:
            pages = int(f.read().split()[0])
        return pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        return 0.0


def _procfs_rss_mb(pid: int) -> float:
    """Linux: `/proc/<pid>/statm`, второе поле — резидентные страницы."""
    try:
        if pid == os.getpid():
            with open("/proc/self/status", encoding="ascii") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) / 1024
        with open(f"/proc/{pid}/statm", encoding="ascii") as f:
            pages = int(f.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        return 0.0


#: PROC_PIDTASKINFO из libproc; структура proc_taskinfo_t — 96 байт.
_PROC_PIDTASKINFO = 4


def _libproc_mb(pid: int) -> tuple[float, float]:
    """macOS: `proc_pidinfo` из libproc — поля `virtual_size` и `resident_size`
    структуры `proc_taskinfo_t`.

    Структуру приходится описывать руками: `ps` — это лишний процесс на каждый
    опрос в цикле ожидания (20 раз в секунду). Сверено с `ps -o vsz= / rss=`:
    расхождение меньше 0.1 %.

    ВАЖНО ПРО VSZ НА macOS: интерпретатор с numpy резервирует под 500 ГБ
    адресного пространства — это пустые резервы, а не память. Поэтому по
    адресному пространству там нельзя ни сравнивать процессы, ни сторожить
    предел: сравнимой величиной остаётся только resident_size.
    """
    try:
        import ctypes

        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        libproc.proc_pidinfo.restype = ctypes.c_int
        libproc.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                         ctypes.c_void_p, ctypes.c_int]

        class TaskInfo(ctypes.Structure):
            _fields_ = [("virtual_size", ctypes.c_uint64),
                        ("resident_size", ctypes.c_uint64),
                        ("user_time", ctypes.c_uint64),
                        ("system_time", ctypes.c_uint64),
                        ("threads_user", ctypes.c_uint64),
                        ("threads_system", ctypes.c_uint64)] + \
                       [(f"_pad{i}", ctypes.c_int32) for i in range(12)]

        info = TaskInfo()
        got = libproc.proc_pidinfo(pid, _PROC_PIDTASKINFO, 0, ctypes.byref(info),
                                   ctypes.sizeof(TaskInfo))
        if got <= 0:
            return 0.0, 0.0
        return (info.virtual_size / (1024 * 1024),
                info.resident_size / (1024 * 1024))
    except Exception:                              # не macOS
        return 0.0, 0.0


def vsize_mb_of(pid: int) -> float:
    """Виртуальное адресное пространство чужого процесса, МБ (0 — не прочлось).

    Ноль означает «не прочлось» — вызывающие это учитывают и не принимают ноль
    за пустой процесс. На macOS величина огромна и сравнивать её не с чем: для
    предела там берётся `rss_mb_of`.
    """
    return _proc_mb(pid)[0]


def rss_mb_of(pid: int) -> float:
    """Занятая (резидентная) память чужого процесса, МБ (0 — не прочлось)."""
    return _proc_mb(pid)[1]


def guard_mb_of(pid: int) -> float:
    """Какую память сторожить у воркера на ЭТОЙ системе, МБ.

    Linux: адресное пространство — его и держит `RLIMIT_AS`, и сторож обязан
    мерить ту же величину, иначе числа расходятся. macOS: ядро предела не
    держит вовсе, а VSZ там — пустые резервы на сотни гигабайт; сторож смотрит
    на реально занятую память.
    """
    return rss_mb_of(pid) if sys.platform == "darwin" else vsize_mb_of(pid)


#: Умеет ли эта система ограничивать воркеру адресное пространство.
#:
#: macOS `setrlimit(RLIMIT_AS)` не применяет («current limit exceeds maximum
#: limit»), и `RLIMIT_DATA` тоже. Проверено опытом: при пределе 1 ГБ процесс
#: спокойно выделяет 2 ГБ. Значит, на macOS единственный предел памяти — это
#: СТОРОЖ по замеру, а не ядро; предупреждать о нём обязан вызывающий.
MEMORY_LIMIT_ENFORCED = sys.platform != "darwin"


def _mb(value: float) -> str:
    """Мегабайты с пробелом между тысячами: «5 705»."""
    return f"{round(value):,}".replace(",", " ")


#: Какую долю ОТПУЩЕННОГО ЗАПАСА (предел минус унаследованное от родителя)
#: должен израсходовать потомок, чтобы смерть от SIGSEGV/SIGABRT считалась
#: нехваткой памяти. Доля — от запаса, а не от предела: потомок рождается с
#: адресами родителя, и у pytest после сотен тестов это 4.3 ГБ при пределе
#: 5.3 — мгновенное падение без единого выделения выходило бы «нехваткой».
#: Не 100 %: опрос мог застать процесс чуть раньше последнего рывка.
MEMORY_DEATH_SHARE = 0.8

_MAGIC = b"DTWR"

#: Файл отметки этапа — задан только в потомке. См. `stage`.
_STAGE_FILE: str | None = None
#: Вложенные этапы: (текст, начало по time.monotonic).
_STAGES: list[tuple[str, float]] = []


def _write_stage() -> None:
    path = _STAGE_FILE
    if path is None:
        return
    text = " → ".join(t for t, _ in _STAGES)
    began = _STAGES[-1][1] if _STAGES else time.monotonic()
    tmp = path + ".tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, f"{began:.3f}\t{text}".encode("utf-8")[:2048])
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except OSError:                                # pragma: no cover
        pass


#: Куда потомок пишет события «сейчас» (`emit`). Задаёт тот, кто запускает
#: задание (сервер); вне задания — None, и `emit` ничего не делает.
_EMIT_FILE: str | None = None
#: Событие длиннее этого не пишется: строка обязана лечь одним `write`.
EMIT_MAX_BYTES = 3500


def emit_to(path: str | None) -> None:
    """В потомке: файл, в который `emit` дописывает события по строке."""
    global _EMIT_FILE
    _EMIT_FILE = str(path) if path else None


def emit(event: dict) -> bool:
    """Сообщить о случившемся ДО конца операции: деталь собрана, ход сборки.

    Операция по-прежнему ничего не знает о сервере и складывает события в
    результат (`events`), но результат приходит, когда досчитано всё: при
    открытии проекта семнадцать деталей из кеша ждали восемнадцатую, и окно
    всё это время показывало пустую сцену (аудит 2026-11-21, §4.6). Здесь
    событие дописывается строкой JSON в файл, который сервер читает по ходу
    задания. Вне сервера (CLI, свой MCP, тесты) файла нет — вызов пустой.

    Строка пишется ОДНИМ `write` в файл, открытый на дозапись: читатель не
    увидит половины. Слишком длинное событие не пишется вовсе — оно всё равно
    придёт с результатом."""
    path = _EMIT_FILE
    if path is None:
        return False
    try:
        line = (json.dumps(event, ensure_ascii=False, default=str) + "\n").encode("utf-8")
        if len(line) > EMIT_MAX_BYTES:
            return False
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
        return True
    except (OSError, TypeError, ValueError):
        return False


@contextmanager
def stage(text: str):
    """Отметить, чем сейчас занят процесс сборки: `with stage("проверка X"):`.

    Потомка, убитого ядром (SIGXCPU, нехватка памяти), уже не спросить, на чём
    он остановился, а «исчерпан предел» без этапа отправлял агента гадать:
    сборка это была или пятая проверка из восьми. Поэтому этап пишется в файл
    рядом с результатом заранее, и родитель читает его, когда потомка нет.
    Этапы вложены: «проверка X → сборка детали Y», и по выходе из вложенного
    отметка возвращается к объемлющему.

    Вне воркера (CLI без изоляции, тесты) файла нет, и запись не делается.
    Стоит она десятки микросекунд: звать на каждую деталь и проверку можно,
    на каждую булеву — незачем.
    """
    if _STAGE_FILE is None:
        # Вне воркера (сервер, CLI без изоляции) — ни файла, ни общего списка:
        # потоки сервера снимали бы этапы друг друга.
        yield
        return
    _STAGES.append((text, time.monotonic()))
    _write_stage()
    try:
        yield
    finally:
        _STAGES.pop()
        _write_stage()


def _read_stage(path: Path | None) -> str:
    """Последний отмеченный этап для сообщения об отказе: « (этап: …, шёл N с)»."""
    if path is None or not path.is_file():
        return ""
    try:
        stamp, _, text = path.read_text(encoding="utf-8").partition("\t")
        spent = time.monotonic() - float(stamp)
    except (OSError, ValueError):                  # pragma: no cover
        return ""
    if not text:
        return ""
    return f" (этап: {text}, шёл {spent:.0f} с)"


#: На сколько понижается приоритет процесса расчёта (см. `_child`).
WORKER_NICE = 10


@dataclass(frozen=True, slots=True)
class WorkerLimits:
    """Пределы для процесса сборки.

    Значения по умолчанию рассчитаны на настольную работу: их хватает любой
    разумной детали, но зациклившийся или разросшийся скрипт упрётся в них
    раньше, чем в них упрётся машина.
    """

    memory_mb: int | None = None
    """Предел адресного пространства (RLIMIT_AS), МБ. None — подобрать на старте.

    ВНИМАНИЕ НА ПОЛ. Предел считается от ВИРТУАЛЬНОГО адресного пространства, а
    не от занятой памяти, и потомок при `fork` наследует всё, что уже отобразил
    родитель. Измерено: numpy, scipy, manifold3d, trimesh и shapely резервируют
    около **1475 МБ** виртуального пространства только на загрузку. Предел ниже
    этого числа не ограничивает скрипт, а убивает процесс сразу при старте с
    невнятным MemoryError.

    ПОЧЕМУ УМОЛЧАНИЕ ПОДБИРАЕТСЯ, А НЕ ЗАДАНО ЧИСЛОМ. Абсолютный потолок в
    4096 МБ держался ровно до появления тетраэдрального мешера: замерено, что
    одна построенная сетка раздувает виртуальное пространство родителя на
    0.9…1.4 ГБ — это резервы пулов потоков OpenMP и арен geogram, а не
    занятая память. После такого родитель занимал 4008 МБ, и ЛЮБАЯ следующая
    изолированная сборка падала с «предел ниже того, что уже занято». Скрипт
    детали к этим отображениям непричастен: он наследует их даром. Поэтому
    умолчание — не потолок, а ЗАПАС СВЕРХ уже отображённого, и ниже
    DEFAULT_MEMORY_MB он не опускается.

    Заданное ЯВНО число остаётся жёстким потолком: подстраивать его значило бы
    молча отменять волю вызывающего.
    """

    headroom_mb: int = 1024
    """Сколько дозволено скрипту СВЕРХ уже отображённого родителем.

    Работает только когда `memory_mb` не задан. Это и есть настоящий бюджет
    детали: всё, что родитель отобразил до `fork`, потомок получает даром.
    """

    cpu_seconds: int = 900
    """Процессорное время. По достижении ядро шлёт SIGXCPU.

    15 минут — решение владельца. Прежние 180 с убивали законную работу:
    скульптура полем в 330 тыс. треугольников собиралась ~60 с, и `export`
    с проверками (каждая пересобирала деталь) выходил за предел. Время
    процессора считается по ВСЕМ потокам, а manifold3d считает в пуле TBB,
    так что астрономических секунд выходит меньше. Это поднятое умолчание,
    а не настройка проекта: проект не может сам себе отпустить больше.
    """

    file_size_mb: int = 512
    """Размер одного файла: защита от скрипта, пишущего бесконечный STL."""

    open_files: int = 256

    wall_seconds: float = 1200.0
    """Астрономическое время. Отдельно от процессорного: процесс может ждать.

    Больше процессорного предела, чтобы долгую работу в одном потоке
    останавливал SIGXCPU с понятным сообщением, а не этот таймер."""

    grace_seconds: float = 2.0
    """Сколько ждать после SIGTERM, прежде чем слать SIGKILL."""

    allow_network: bool = False

    project_dir: str = ""
    """Каталог проекта. Задан — в потомке ставятся перила из `build.sandbox`:
    ограниченный список импортов для скриптов проекта и запрет записи за его
    пределы. Пусто — перил нет (например, у операций, не открывающих проект)."""

    extra_imports: tuple[str, ...] = ()
    """Что проект разрешает скриптам сверх списка по умолчанию."""

    def memory_limit_mb(self) -> int:
        """Предел в мегабайтах — заданный или подобранный ЗДЕСЬ И СЕЙЧАС.

        Подбор происходит на старте воркера, а не при создании этого объекта:
        пределы часто держат в константе модуля, созданной при импорте, когда
        родитель ещё маленький.

        На macOS считается от ЗАНЯТОЙ памяти, а не от адресного пространства:
        интерпретатор там резервирует сотни гигабайт пустых адресов, и подбор
        от них дал бы предел, которого на ноутбуке нет ФИЗИЧЕСКИ.
        """
        if self.memory_mb is not None:
            return int(self.memory_mb)
        if not MEMORY_LIMIT_ENFORCED:
            occupied = rss_mb_of(os.getpid())
            return max(DEFAULT_MEMORY_MB, int(occupied) + self.headroom_mb)
        return max(DEFAULT_MEMORY_MB, int(current_vsize_mb()) + self.headroom_mb)


@dataclass(frozen=True, slots=True)
class WorkerOutcome:
    """Чем кончилась сборка в процессе."""

    status: str                      # ok | error | timeout | cancelled | killed
    payload: dict = field(default_factory=dict)
    mesh: bytes = b""
    ms: float = 0.0
    exit_code: int | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def limits_for(kwargs: dict, base: WorkerLimits, *,
               headroom_mb: int | None = None) -> WorkerLimits:
    """Пределы для конкретного вызова операции.

    Каталог проекта известен только из аргументов самой операции, а от него
    зависят перила (`build.sandbox`). Вывод живёт ЗДЕСЬ, в одном месте, потому
    что поверхностей три — CLI, MCP и сервер, — и, будучи повторённым, он
    однажды разойдётся: ровно так перила и оказались выключены на сервере,
    работая при этом в CLI.

    Операции без проекта (состояние кеша, диагностика отдельного файла) идут
    без перил: ограничивать там нечего.

    `headroom_mb` — запас памяти, объявленный самой операцией
    (`@op(memory_headroom_mb=...)`): чужой меш весит столько, сколько весит
    файл, и гигабайт, достаточный скрипту детали, модели в полтора миллиона
    треугольников мал. Запас только расширяет умолчание; явно заданный
    потолок (`memory_mb`) он не трогает.
    """
    project = kwargs.get("project")
    if project and not base.project_dir:
        base = replace(base, project_dir=str(project))
    if headroom_mb and base.memory_mb is None and headroom_mb > base.headroom_mb:
        base = replace(base, headroom_mb=int(headroom_mb))
    return base


# ------------------------------------------------------------------ в дочернем
def _apply_limits(limits: WorkerLimits) -> None:
    def setrl(what, soft, hard=None):
        try:
            cur_soft, cur_hard = resource.getrlimit(what)
            hard = cur_hard if hard is None else hard
            if cur_hard != resource.RLIM_INFINITY:
                soft = min(soft, cur_hard)
                hard = min(hard, cur_hard)
            resource.setrlimit(what, (soft, hard))
        except (ValueError, OSError):            # pragma: no cover
            pass                                  # предел недоступен — не повод падать

    # На macOS RLIMIT_AS не ставится (см. MEMORY_LIMIT_ENFORCED): там этот
    # вызов молча ничего не делает, и память сторожит опрос в `wait`.
    setrl(resource.RLIMIT_AS, limits.memory_limit_mb() * 1024 * 1024)
    setrl(resource.RLIMIT_CPU, limits.cpu_seconds)
    setrl(resource.RLIMIT_FSIZE, limits.file_size_mb * 1024 * 1024)
    setrl(resource.RLIMIT_NOFILE, limits.open_files)
    setrl(resource.RLIMIT_CORE, 0)                # дампы ядра нам не нужны


def _block_network() -> None:
    """Запретить ИСХОДЯЩИЕ соединения, но не сокеты вообще.

    Первая версия подменяла `socket.socket` целиком — и всё разваливалось:
    asyncio при запуске создаёт себе локальную пару сокетов для пробуждения
    цикла, натыкался на запрет и падал ещё до начала работы. Локальные сокеты
    нужны и многопроцессности, и очередям.

    Поэтому запрещается именно выход наружу: подключение к адресам IPv4/IPv6 и
    разрешение имён. Локальные (AF_UNIX) и пары сокетов работают как прежде.
    """
    import socket

    message = ("скрипту детали сеть запрещена. Если данные нужны снаружи — "
               "положите их в проект и читайте из файла")

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guarded(original):
        def wrapper(self, address, *a, **k):
            if self.family in (socket.AF_INET, socket.AF_INET6):
                raise PermissionError(message)
            return original(self, address, *a, **k)
        return wrapper

    socket.socket.connect = guarded(real_connect)        # type: ignore[assignment]
    socket.socket.connect_ex = guarded(real_connect_ex)  # type: ignore[assignment]

    def refuse(*_a, **_k):
        raise PermissionError(message)

    socket.create_connection = refuse                    # type: ignore[assignment]
    socket.getaddrinfo = refuse                          # type: ignore[assignment]


def _write_payload(path: Path, payload: dict, mesh: bytes) -> None:
    blob = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(_MAGIC)
        f.write(struct.pack("<QQ", len(blob), len(mesh)))
        f.write(blob)
        f.write(mesh)
    os.replace(tmp, path)             # атомарно: недописанного не бывает


def _read_payload(path: Path) -> tuple[dict, bytes]:
    data = path.read_bytes()
    if len(data) < 20 or data[:4] != _MAGIC:
        raise ValueError("результат сборки не читается: нет заголовка")
    nb, nm = struct.unpack("<QQ", data[4:20])
    payload = pickle.loads(data[20:20 + nb])
    return payload, data[20 + nb:20 + nb + nm]


def _die_with_parent(parent_pid: int) -> None:
    """Умереть вместе с родителем.

    Своя группа процессов (`setsid`) нужна, чтобы отмена снимала потомка
    целиком, — но она же отрывает его от терминала: `Ctrl-C` или `timeout`,
    убившие командную строку, до потомка больше не доходят. Замер: после
    `timeout 12 datum info` на зависшем проекте воркер остался жить с ppid 1 и
    жёг 95 % процессора до своего предела.

    `PR_SET_PDEATHSIG` просит ядро прислать SIGKILL, когда родитель умрёт.
    Родитель мог умереть и раньше, чем мы успели попросить, — поэтому сразу
    после просьбы сверяем, жив ли он.
    """
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        PR_SET_PDEATHSIG = 1
        libc.prctl(PR_SET_PDEATHSIG, int(signal.SIGKILL), 0, 0, 0)
    except Exception:
        # macOS и прочие без prctl. Там роль ядра играет поток-сторож: раз в
        # полсекунды сверяет родителя и уходит по SIGKILL, если тот умер.
        # Иначе сборка переживает и `Ctrl-C`, и закрытое окно.
        if os.getppid() != parent_pid:
            os._exit(1)
        _watch_parent_in_thread(parent_pid)
        return
    if os.getppid() != parent_pid:
        os._exit(1)


#: Как часто сторож без prctl проверяет, жив ли родитель.
PARENT_WATCH_SECONDS = 0.5


def _watch_parent_in_thread(parent_pid: int) -> None:
    """Умереть вместе с родителем там, где `PR_SET_PDEATHSIG` нет.

    Поток, а не сигнал: SIGKILL, посланный себе, снимает процесс целиком, и
    обработчик в Python для этого не нужен. Поток объявлен daemon — он не
    держит выход.
    """
    import threading

    def watch() -> None:
        while True:
            time.sleep(PARENT_WATCH_SECONDS)
            if os.getppid() != parent_pid:
                os.kill(os.getpid(), signal.SIGKILL)
                return

    threading.Thread(target=watch, name="datum-parent-watch", daemon=True).start()


#: Сколько арен malloc разрешить потомку. См. `_cap_malloc_arenas`.
MALLOC_ARENAS = 2


def _cap_malloc_arenas() -> None:
    """Не давать glibc заводить по арене malloc на каждый поток.

    Предел памяти потомка — предел АДРЕСНОГО ПРОСТРАНСТВА, а glibc резервирует
    каждому потоку свою арену в 64 МБ адресов. manifold3d считает в пуле TBB на
    всех ядрах, и на 16 потоках это около гигабайта резервов, в которых нет ни
    байта данных. Замерено на меше в 1.48 млн треугольников: прирост VmPeak
    2200 МБ при арен по умолчанию и 1321 при двух, время то же (3.07 против
    3.09 с), занятая память (VmHWM) та же — 1674 МБ.

    Переменная `MALLOC_ARENA_MAX` тут не поможет: glibc читает её при старте
    процесса, а потомок получен fork'ом из уже работающего. `mallopt` действует
    сразу.
    """
    try:
        import ctypes

        M_ARENA_MAX = -8
        ctypes.CDLL(None).mallopt(M_ARENA_MAX, MALLOC_ARENAS)
    except Exception:                 # pragma: no cover — не glibc
        pass


def _child(fn, args: tuple, kwargs: dict, out_path: str,
           limits: WorkerLimits, parent_pid: int = 0) -> None:
    """Точка входа дочернего процесса. Возврата отсюда нет — только os._exit."""
    code = 0
    try:
        os.setsid()                   # своя группа: убить можно целиком
    except OSError:                   # pragma: no cover
        pass
    if parent_pid:
        _die_with_parent(parent_pid)
    _cap_malloc_arenas()
    # Расчёт уступает дорогу человеку: сборка, МКЭ и нарезка занимают все ядра
    # минутами («только там что-то нагружает мой процессор» — владелец), а окно,
    # браузер и редактор при этом должны отзываться. На свободной машине
    # приоритет ничего не замедляет: он решает только, кому ждать при споре за
    # ядро. Наследуется потомками (мешер, слайсер).
    try:
        os.nice(WORKER_NICE)
    except OSError:                   # pragma: no cover
        pass

    # Потомок наследует стек родителя на момент fork. Если в нём включён
    # faulthandler (а pytest включает его по умолчанию), падение дочернего
    # процесса печатает ЧУЖОЙ стек — из родителя, — и выглядит как падение
    # приложения, которым не является. Выключаем.
    try:
        import faulthandler

        faulthandler.disable()
    except Exception:                 # pragma: no cover
        pass
    global _STAGE_FILE
    _STAGE_FILE = str(Path(out_path).with_name("stage.txt"))
    _STAGES.clear()                   # унаследованы от родителя fork'ом
    _apply_limits(limits)
    if not limits.allow_network:
        _block_network()
    if limits.project_dir:
        # Перила ставятся ПОСЛЕ пределов и запрета сети, но до работы: с этого
        # момента запись разрешена только в проект и во временные каталоги, а
        # туда воркер и пишет свой результат.
        from datum.build.sandbox import install as install_sandbox

        install_sandbox(limits.project_dir, extra_imports=limits.extra_imports)

    t0 = time.perf_counter()
    try:
        payload, mesh = fn(*args, **kwargs)
        payload = dict(payload)
        payload["ms"] = (time.perf_counter() - t0) * 1000
        _write_payload(Path(out_path), payload, mesh)
    except MemoryError:
        code = 3
        try:
            _write_payload(Path(out_path),
                           {"status": "error", "error": {
                               "reason": "memory",
                               "message": f"сборка не уложилась в "
                                          f"{limits.memory_mb} МБ памяти"}},
                           b"")
        except Exception:             # pragma: no cover
            pass
    except BaseException as e:
        code = 1
        try:
            _write_payload(Path(out_path),
                           {"status": "error", "error": {
                               "reason": type(e).__name__, "message": str(e)}},
                           b"")
        except Exception:             # pragma: no cover
            pass
    finally:
        os._exit(code)                # без уборки интерпретатора: она тут лишняя


# ------------------------------------------------------------------ в родителе
class BuildWorker:
    """Один запуск в отдельном процессе с пределами и отменой."""

    def __init__(self, fn, args: tuple = (), kwargs: dict | None = None, *,
                 limits: WorkerLimits | None = None):
        self.fn = fn
        self.args = args
        self.kwargs = kwargs or {}
        self.limits = limits or WorkerLimits()
        self.pid: int | None = None
        # Каталог заводится не здесь, а в start(): если запуск не состоится
        # (например, предел памяти ниже допустимого), незачем оставлять мусор.
        self._dir: Path | None = None
        self._out: Path | None = None
        self._started = 0.0
        self._cancelled = False
        # Пик адресного пространства потомка по опросу в `wait`: по нему
        # смерть у предела памяти отличается от падения ядра.
        self.peak_vsize_mb = 0.0
        self._inherited_mb = 0.0

    def __enter__(self) -> "BuildWorker":
        return self

    def __exit__(self, *exc) -> None:
        if self.pid is not None:
            self.cancel()
            self.wait()
        self._cleanup()

    def __del__(self) -> None:                   # pragma: no cover — страховка
        try:
            self._cleanup()
        except Exception:
            pass

    # ------------------------------------------------------------ управление
    def start(self) -> "BuildWorker":
        if self.pid is not None:                 # pragma: no cover
            raise RuntimeError("уже запущен")

        # Проверяем ДО fork: иначе потомок умрёт при старте с MemoryError,
        # и понять, что дело в пределе, а не в скрипте детали, будет нельзя.
        # На macOS проверять нечего: там ядро предел не держит, и низкий предел
        # — это не срыв запуска, а решение сторожа (см. `wait`). Базовая линия
        # там всё равно нужна, но своя: занятая память родителя, с которой
        # потомок рождается, — иначе рост сверх унаследованного не посчитать.
        occupied = (rss_mb_of(os.getpid()) if not MEMORY_LIMIT_ENFORCED
                    else current_vsize_mb())
        self._inherited_mb = occupied           # с этим потомок родится
        resolved = self.limits.memory_limit_mb()
        if MEMORY_LIMIT_ENFORCED and occupied and resolved < occupied + 128:
            raise LimitTooLow(
                f"предел памяти {resolved} МБ ниже того, что уже "
                f"занимает сам интерпретатор ({occupied:.0f} МБ виртуального "
                f"пространства: столько резервируют numpy, manifold3d и прочие). "
                f"Возьмите хотя бы {int(occupied) + 512} МБ")
        # В потомок уходит РАЗРЕШЁННЫЙ предел: подбирать его после fork поздно —
        # там уже другое адресное пространство, и число вышло бы другим.
        self.limits = replace(self.limits, memory_mb=resolved)

        self._dir = Path(tempfile.mkdtemp(prefix="datum-worker-"))
        self._out = self._dir / "result.bin"
        self._started = time.perf_counter()
        parent = os.getpid()
        pid = os.fork()
        if pid == 0:
            _child(self.fn, self.args, self.kwargs, str(self._out), self.limits,
                   parent_pid=parent)
            os._exit(99)                          # pragma: no cover — сюда не доходит
        self.pid = pid
        return self

    def cancel(self) -> None:
        """Отменить: сперва SIGTERM, потом, если не помогло, SIGKILL."""
        self._cancelled = True
        self._signal_group(signal.SIGTERM)

    def _signal_group(self, sig: int) -> None:
        if self.pid is None:
            return
        try:
            os.killpg(os.getpgid(self.pid), sig)
        except (ProcessLookupError, PermissionError):
            try:
                os.kill(self.pid, sig)
            except ProcessLookupError:
                pass

    def _reap(self, block: bool) -> int | None:
        if self.pid is None:
            return None
        try:
            pid, status = os.waitpid(self.pid, 0 if block else os.WNOHANG)
        except ChildProcessError:                 # pragma: no cover
            return 0
        if pid == 0:
            return None
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        if os.WIFSIGNALED(status):
            return -os.WTERMSIG(status)
        return None                               # pragma: no cover

    def wait(self) -> WorkerOutcome:
        """Дождаться результата, соблюдая пределы по времени."""
        if self.pid is None:                      # pragma: no cover
            raise RuntimeError("не запущен")

        deadline = self._started + self.limits.wall_seconds
        code: int | None = None
        timed_out = False
        over_memory = False
        polled = 0.0
        while True:
            code = self._reap(block=False)
            if code is not None:
                break
            now = time.perf_counter()
            if now - polled >= 0.02:
                polled = now
                self.peak_vsize_mb = max(self.peak_vsize_mb, guard_mb_of(self.pid))
                # Там, где ядро предел не держит (macOS), держит этот сторож:
                # процесс, перешедший предел адресного пространства, гасится
                # здесь — иначе на 8 ГБ ноутбука он забирает машину целиком.
                if not MEMORY_LIMIT_ENFORCED and self.peak_vsize_mb > self._limit_mb_soft():
                    over_memory = True
                    break
            if self._cancelled or now >= deadline:
                timed_out = timed_out or now >= deadline
                break
            time.sleep(0.005)

        if code is None:
            # Не завершился сам: гасим. SIGTERM не прерывает вычисление в C++,
            # поэтому вслед обязателен SIGKILL.
            self._signal_group(signal.SIGTERM)
            till = time.perf_counter() + self.limits.grace_seconds
            while time.perf_counter() < till:
                code = self._reap(block=False)
                if code is not None:
                    break
                time.sleep(0.005)
            if code is None:
                self._signal_group(signal.SIGKILL)
                code = self._reap(block=True)

        ms = (time.perf_counter() - self._started) * 1000
        outcome = self._collect(code, ms, timed_out, over_memory)
        self._cleanup()
        return outcome

    def _limit_mb_soft(self) -> float:
        """Предел для сторожа по замеру. Ноль — сторож выключен.

        Запас сверх предела даётся потому, что замер идёт раз в 20 мс: между
        двумя опросами процесс успевает выделить заметный кусок, и срабатывание
        ровно на пределе било бы по законной работе вплотную к нему.
        """
        limit = self.limits.memory_limit_mb()
        return limit * 1.1 if limit else 0.0

    def _collect(self, code: int | None, ms: float, timed_out: bool,
                 over_memory: bool = False) -> WorkerOutcome:
        payload: dict = {}
        mesh = b""
        if self._out is not None and self._out.is_file():
            try:
                payload, mesh = _read_payload(self._out)
            except Exception as e:                # pragma: no cover
                payload = {"status": "error",
                           "error": {"reason": "payload", "message": str(e)}}

        stage = _read_stage(self._out.with_name("stage.txt") if self._out else None)
        if over_memory:
            # Сторож по замеру (macOS): ядро предела не держит, гасили мы.
            return WorkerOutcome(
                status="killed", payload=payload, ms=ms, exit_code=code,
                message=(f"не хватило памяти: процесс дошёл до "
                         f"{_mb(self.peak_vsize_mb)} МБ занятой памяти "
                         f"при пределе {_mb(self.limits.memory_limit_mb())} МБ "
                         f"и был остановлен{stage}. Здесь предел памяти держит "
                         f"сам Datum (на этой системе ядро его не применяет): "
                         f"уменьшите сетку, размер скульптуры или запас памяти"))
        if timed_out:
            return WorkerOutcome(
                status="timeout", payload=payload, ms=ms, exit_code=code,
                message=(f"сборка не уложилась в {self.limits.wall_seconds:g} с "
                         f"и была прервана{stage}"))
        if self._cancelled:
            return WorkerOutcome(status="cancelled", payload=payload, ms=ms,
                                 exit_code=code, message="сборка отменена")
        if payload.get("status") == "ok":
            return WorkerOutcome(status="ok", payload=payload, mesh=mesh, ms=ms,
                                 exit_code=code)
        if payload.get("status") == "error":
            err = payload.get("error", {})
            return WorkerOutcome(status="error", payload=payload, ms=ms,
                                 exit_code=code,
                                 message=f"{err.get('reason','')}: {err.get('message','')}")
        # Результата нет вовсе — процесс умер раньше, чем успел записать.
        if code is not None and code < 0:
            sig = -code
            names = {signal.SIGKILL: "SIGKILL", signal.SIGTERM: "SIGTERM",
                     signal.SIGSEGV: "SIGSEGV", signal.SIGXCPU: "SIGXCPU",
                     signal.SIGABRT: "SIGABRT"}
            name = names.get(sig, f"сигнал {sig}")
            hint = ""
            if sig == signal.SIGXCPU:
                hint = (f" — исчерпан предел процессорного времени "
                        f"{self.limits.cpu_seconds} с")
            elif sig in (signal.SIGSEGV, signal.SIGABRT) and self._near_memory_limit():
                # Отказанный malloc в native-коде — это не MemoryError, а
                # разыменованный NULL. Сказать «упало ядро» значило бы
                # отправить человека искать дефект в модели, которого нет.
                limit = self.limits.memory_limit_mb()
                return WorkerOutcome(
                    status="killed", ms=ms, exit_code=code,
                    message=(f"не хватило памяти: процесс дошёл до "
                             f"{_mb(self.peak_vsize_mb)} МБ адресного пространства "
                             f"при пределе {_mb(limit)} МБ и упал ({name}) на "
                             f"отказанном выделении{stage}"))
            elif sig in (signal.SIGSEGV, signal.SIGABRT):
                hint = " — упало геометрическое ядро"
            return WorkerOutcome(status="killed", ms=ms, exit_code=code,
                                 message=f"процесс сборки убит: {name}{hint}{stage}")
        return WorkerOutcome(status="killed", ms=ms, exit_code=code,
                             message=f"процесс сборки завершился без результата "
                                     f"(код {code})")

    def _near_memory_limit(self) -> bool:
        limit = self.limits.memory_limit_mb()
        room = limit - self._inherited_mb
        if not limit or room <= 0:
            return False
        return self.peak_vsize_mb - self._inherited_mb >= MEMORY_DEATH_SHARE * room

    def _cleanup(self) -> None:
        if self._dir is None:
            return
        # Сносим каталог целиком, а не только result.bin: если потомка убили
        # посреди записи, рядом остаётся result.bin.tmp, и rmdir не проходит.
        import shutil

        try:
            shutil.rmtree(self._dir, ignore_errors=True)
        finally:
            self._dir = None
            self._out = None


def run_in_worker(fn, *args, limits: WorkerLimits | None = None,
                  **kwargs) -> WorkerOutcome:
    """Запустить и дождаться. `fn` обязана вернуть (словарь, байты меша)."""
    return wait_or_cancel(BuildWorker(fn, args, kwargs, limits=limits).start())


def wait_or_cancel(worker: "BuildWorker") -> WorkerOutcome:
    """Дождаться воркера, а при прерывании — снять его, а не бросить.

    `Ctrl-C` прилетает в ожидание как KeyboardInterrupt; без этого потомок в
    своей группе процессов доживал бы до предела по времени.
    """
    try:
        return worker.wait()
    except BaseException:
        worker.cancel()
        try:
            worker.wait()
        finally:
            raise
