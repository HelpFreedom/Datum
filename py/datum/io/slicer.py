"""Нарезка в ElegooSlicer без окна: проект 3MF с профилем и G-code.

ПОЧЕМУ ПРОЕКТ ПИШЕТ САМ СЛАЙСЕР. Агент собирал проект 3MF руками
(`make3mf.py` подставки под мышь): брал готовый файл человека как шаблон и
подменял сетку и пластик. Datum на чужой файл опираться не может, а писать
формат Bambu/Orca самому значит угадывать его. У слайсера же есть консольный
режим: он берёт наши профили и модель, нарезает и сам записывает проект
(`--export-3mf`), и этот проект откроется в окне слайсера ровно как свой.

ПРОФИЛИ РАЗРЕШАЮТСЯ ЗДЕСЬ, А НЕ СЛАЙСЕРОМ — замер, на котором это решено.
Консольный режим НЕ ДОСТРАИВАЕТ цепочку `inherits` загруженного профиля: берёт
значения самого файла, а остальное — из встроенных умолчаний. Нарезка
проходит «успешно», а в ней ширина линий 0, ускорение 500, плотность пластика
0 — 146 ключей против проекта, записанного окном слайсера. После разрешения
цепочки здесь — 29, и все они служебные (кроме типа стола, см. `BED_TYPES`).

ИМЯ РАЗРЕШАЕТСЯ В СПИСКЕ СВОЕГО ПРОИЗВОДИТЕЛЯ. В каталоге системных профилей
22 имени повторяются: `Elegoo PLA @base` есть и у Elegoo, и в библиотеке Orca,
`fdm_process_common` — и в `Custom/`, и в `Elegoo/`. Агент взял первое
попавшееся и получил чужой `filament_id`. Здесь родитель ищется в манифесте
производителя (`Elegoo.json`) в списке того же рода; не нашлось ровно одного
— отказ, а не догадка.

ЧТО НАРЕЗАНО — ПРОВЕРЯЕТСЯ. Подвал G-code — полный действующий конфиг
слайсера (`; ключ = значение`): профили, слой, тип стола и каждая правка
сверяются с ним, число треугольников — с деталью. Не сошлось — файлов нет,
есть отказ. Лучше нарезать в окне слайсера, чем напечатать по ошибке.
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["SlicerError", "Profiles", "Choice", "Sliced", "choose", "slice_model",
           "slicer_command", "system_dir", "parse_gcode", "BED_TYPES"]

#: Где окно слайсера держит системные профили (их обновляет само окно).
SYSTEM_DIR = Path("~/.config/ElegooSlicer/system")

#: Номер типа стола в профиле машины (`default_bed_type`) → имя для проекта.
#: Консольный режим умолчание машины не применяет и ставит «Cool Plate» — а от
#: типа стола зависит его температура. Здесь только сверенное с проектом,
#: записанным окном слайсера для CC2; неизвестный номер — предупреждение.
BED_TYPES = {"4": "Textured PEI Plate"}

#: Профиль принтера из datum.toml → модель в профилях Elegoo.
PRINTERS = {"elegoo-cc2": "Elegoo Centauri Carbon 2"}

#: Сколько ждать слайсер. Скульптура в 330 тыс. треугольников режется за 7 с.
TIMEOUT_S = 600


class SlicerError(RuntimeError):
    """Нарезать нельзя или нарезано не то — с причиной."""


def system_dir() -> Path:
    return Path(os.environ.get("DATUM_ELEGOO_PROFILES", SYSTEM_DIR)).expanduser()


def slicer_command() -> list[str]:
    """Как запустить слайсер: `DATUM_ELEGOO_SLICER` или `elegooslicer` в PATH.

    `elegooslicer` у владельца — обёртка, запускающая AppImage в distrobox
    (новее glibc); с аргументами командной строки она запускает тот же
    консольный режим.
    """
    env = os.environ.get("DATUM_ELEGOO_SLICER", "").strip()
    if env:
        return shlex.split(env)
    for name in ("elegooslicer", "elegoo-slicer", "ElegooSlicer"):
        found = shutil.which(name)
        if found:
            return [found]
    raise SlicerError("ElegooSlicer не найден: нет `elegooslicer` в PATH и не задан "
                      "DATUM_ELEGOO_SLICER. Нарежьте в окне слайсера файл из `export`")


# ================================================================== профили
class Profiles:
    """Системные профили одного производителя, по его манифесту."""

    KINDS = {"machine": "machine_list", "process": "process_list", "filament": "filament_list"}

    def __init__(self, root: Path | None = None, vendor: str = "Elegoo"):
        self.root = Path(root or system_dir()).expanduser()
        self.vendor = vendor
        manifest = self.root / f"{vendor}.json"
        if not manifest.is_file():
            raise SlicerError(f"нет профилей {vendor}: {manifest} не найден — "
                              f"откройте окно ElegooSlicer, оно их скачает")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.lists: dict[str, list[dict]] = {k: list(data.get(v, [])) for k, v in self.KINDS.items()}
        self._cache: dict[tuple[str, str], dict] = {}

    def raw(self, kind: str, name: str) -> dict:
        hits = [x for x in self.lists[kind] if x.get("name") == name]
        if len(hits) != 1:
            what = "нет" if not hits else f"{len(hits)} одноимённых"
            raise SlicerError(f"профиль {kind} {name!r}: {what} в манифесте {self.vendor}")
        path = self.root / self.vendor / hits[0]["sub_path"]
        return json.loads(path.read_text(encoding="utf-8"))

    def resolve(self, kind: str, name: str) -> dict:
        """Профиль со всей цепочкой `inherits`: родитель — в том же списке."""
        key = (kind, name)
        if key not in self._cache:
            seen: list[str] = []
            chain: list[dict] = []
            current = name
            while current:
                if current in seen:
                    raise SlicerError(f"профиль {name!r}: цикл наследования {seen + [current]}")
                seen.append(current)
                d = self.raw(kind, current)
                chain.append(d)
                current = d.get("inherits", "")
            flat: dict = {}
            for d in reversed(chain):
                flat.update(d)
            flat.pop("inherits", None)
            self._cache[key] = flat
        return dict(self._cache[key])

    def presets(self, kind: str, *, printer: str | None = None) -> list[str]:
        """Имена профилей, которые можно выбрать (не базовые), и для принтера."""
        out = []
        for item in self.lists[kind]:
            if str(self.raw(kind, item["name"]).get("instantiation", "")).lower() != "true":
                continue
            # Совместимость наследуется: у «0.12mm Fine» её нет в самом файле,
            # она у родителя «0.20mm Standard».
            compatible = self.resolve(kind, item["name"]).get("compatible_printers") or []
            if printer and kind != "machine" and printer not in compatible:
                continue
            out.append(item["name"])
        return sorted(out)


@dataclass(frozen=True)
class Choice:
    printer: str
    process: str
    filament: str
    bed_type: str            # пусто — умолчание слайсера
    notes: tuple[str, ...] = ()


def _printer_name(profile: str, nozzle: float) -> str:
    """`elegoo-cc2-0.4` → «Elegoo Centauri Carbon 2 0.4 nozzle»."""
    m = re.fullmatch(r"(.+?)-(\d+(?:\.\d+)?)", profile.strip())
    model_key, n = (m.group(1), float(m.group(2))) if m else (profile.strip(), nozzle)
    if model_key not in PRINTERS:
        raise SlicerError(f"принтер {profile!r} из datum.toml не знаком нарезке; "
                          f"известны: {sorted(PRINTERS)}")
    if not math.isclose(n, nozzle, abs_tol=1e-6):
        raise SlicerError(f"в datum.toml профиль {profile!r} (сопло {n:g}), а nozzle = "
                          f"{nozzle:g} — какое верно, знает человек")
    return f"{PRINTERS[model_key]} {n:g} nozzle"


def choose(profiles: Profiles, *, printer_profile: str, nozzle: float, layer: float,
           material: str, process: str | None = None,
           filament: str | None = None) -> Choice:
    """Профили принтера, процесса и пластика — по datum.toml, без догадок.

    Процесс — с тем же слоем, что в datum.toml, из совместимых с принтером;
    из нескольких — «Standard». Пластик — «Elegoo <материал> @…»: у PLA их
    два десятка (Basic, PRO, Silk…), и выбрать за человека другой нельзя.
    Явно названные `process`/`filament` берутся как есть, но совместимость с
    принтером проверяется и у них.
    """
    printer = _printer_name(printer_profile, nozzle)
    if printer not in profiles.presets("machine"):
        raise SlicerError(f"в профилях нет принтера {printer!r}")
    machine = profiles.resolve("machine", printer)
    notes: list[str] = []

    processes = profiles.presets("process", printer=printer)
    if process:
        if process not in processes:
            raise SlicerError(f"процесс {process!r} не подходит к {printer}; есть: "
                              + ", ".join(processes))
    else:
        same = [p for p in processes
                if math.isclose(float(profiles.resolve("process", p).get("layer_height", 0)),
                                layer, abs_tol=1e-6)]
        if not same:
            raise SlicerError(f"у {printer} нет процесса со слоем {layer:g} мм (datum.toml); "
                              f"есть: " + ", ".join(processes))
        standard = [p for p in same if " Standard " in f" {p} "]
        process = (standard or same)[0]
        if len(same) > 1:
            notes.append(f"процесс со слоем {layer:g}: взят {process!r} из {len(same)}")

    filaments = profiles.presets("filament", printer=printer)
    if filament:
        if filament not in filaments:
            raise SlicerError(f"пластик {filament!r} не подходит к {printer}; есть: "
                              + ", ".join(filaments))
    else:
        wanted = f"{profiles.vendor} {material}".strip()
        exact = [f for f in filaments if f.split(" @")[0] == wanted]
        if len(exact) != 1:
            kin = [f for f in filaments
                   if str(_first(profiles.resolve("filament", f).get("filament_type"))).upper()
                   == material.upper()]
            raise SlicerError(f"пластик для {material!r}: профиля «{wanted} @…» "
                              + ("нет" if not exact else f"{len(exact)}")
                              + f"; того же типа: {', '.join(kin) or 'нет'} — назовите filament")
        filament = exact[0]

    bed_code = str(machine.get("default_bed_type", ""))
    bed = BED_TYPES.get(bed_code, "")
    if bed_code and not bed:
        notes.append(f"тип стола {bed_code} в профиле машины не сверен — оставлено "
                     f"умолчание слайсера; проверьте стол и его температуру")
    return Choice(printer, process, filament, bed, tuple(notes))


def _first(value):
    return value[0] if isinstance(value, list) and value else value


# ==================================================================== G-code
def parse_gcode(path: Path) -> dict[str, str]:
    """Сводка и действующий конфиг из G-code слайсера: `; ключ = значение`.

    Читается только хвост: конфиг и сводка — в конце файла, а сам файл
    бывает в десятки мегабайт.
    """
    size = path.stat().st_size
    with open(path, "rb") as f:
        f.seek(max(0, size - 400_000))
        tail = f.read().decode("utf-8", "replace")
    out: dict[str, str] = {}
    for line in tail.splitlines():
        if line.startswith("; ") and " = " in line:
            key, _, value = line[2:].partition(" = ")
            out.setdefault(key.strip(), value.strip())
    return out


#: Линии G-code, которые НЕ деталь: прочистка в начале, юбка, кайма, поддержки.
NOT_PART = frozenset({"Custom", "Skirt", "Brim", "Support", "Support interface",
                      "Support transition", "Prime tower", "Wipe tower"})


def part_grams(path: Path, *, density: float, diameter: float = 1.75) -> float:
    """Пластик САМОЙ детали в G-code, г: чистая подача (с откатами) по линиям
    `;TYPE:`, кроме `NOT_PART`.

    «filament used [g]» слайсера включает прочистку в начале печати (~0.5 г у
    Elegoo CC2) и кайму: у прокладки Gear Cube 0.60 г при самой детали 0.06 —
    сверять массу Datum с ним значило бы сверять с прочисткой. Подача — только
    относительная (M83): так пишет ElegooSlicer; абсолютная — отказ, чтобы не
    насчитать вздор."""
    import math as _m
    import re as _re

    area = _m.pi * (diameter / 2) ** 2
    total = 0.0
    kind = "Custom"
    relative = None
    number = _re.compile(r"\bE(-?\d*\.?\d+)")
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith(";TYPE:"):
                kind = line[6:].strip()
            elif line.startswith("M83"):
                relative = True
            elif line.startswith("M82"):
                relative = False
            elif line.startswith(("G1 ", "G0 ", "G2 ", "G3 ")) and kind not in NOT_PART:
                m = number.search(line)
                if m:
                    if relative is not True:
                        raise SlicerError(f"{path.name}: подача абсолютная (M82) — граммы "
                                          f"детали так не сосчитать")
                    total += float(m.group(1))
    return total * area * density / 1000.0


def _same(value: str, wanted) -> bool:
    """Значение из подвала против задуманного (списки — через запятую).

    Числа сравниваются числами, но процент — только с процентом: «50%» и
    «50» у слайсера разное (доля и миллиметры).
    """
    got = value.strip().strip('"')
    want = wanted[0] if isinstance(wanted, list) and len(wanted) == 1 else wanted
    if isinstance(want, list):
        want = ",".join(str(w) for w in want)
    want = str(want).strip().strip('"')
    if got == want:
        return True
    if got.endswith("%") != want.endswith("%"):
        return False
    try:
        return math.isclose(float(got.rstrip("%")), float(want.rstrip("%")), rel_tol=1e-6)
    except ValueError:
        return False


# =================================================================== нарезка
@dataclass(frozen=True)
class Sliced:
    project: Path            # проект 3MF (с нарезкой внутри)
    gcode: Path
    choice: Choice
    overrides: dict
    time: str                # «2h 56m 39s», как пишет слайсер
    grams: float
    layers: int
    triangles: int
    warnings: tuple[str, ...] = ()
    config: dict = field(default_factory=dict, repr=False)


def slice_model(model: Path, out_project: Path, out_gcode: Path, *, profiles: Profiles,
                choice: Choice, triangles: int, overrides: dict | None = None,
                work: Path, command: list[str] | None = None,
                timeout: float = TIMEOUT_S) -> Sliced:
    """Нарезать проверенный 3MF и положить проект и G-code, сверив их.

    `work` — рабочий каталог внутри проекта (его видит и контейнер слайсера).
    `triangles` — сколько треугольников у детали: слайсер обязан нарезать ровно
    их. Правки `overrides` — только к существующим ключам профилей.
    """
    def text(v) -> str:
        # Булево в профилях слайсера — «1»/«0»; str(True) дало бы «True», и
        # сверка с подвалом отказала бы с непонятным сообщением.
        return ("1" if v else "0") if isinstance(v, bool) else str(v)

    overrides = {str(k): ([text(x) for x in v] if isinstance(v, list) else text(v))
                 for k, v in (overrides or {}).items()}
    machine = profiles.resolve("machine", choice.printer)
    process = profiles.resolve("process", choice.process)
    filament = profiles.resolve("filament", choice.filament)
    unknown = [k for k in overrides if k not in machine and k not in process and k not in filament]
    if unknown:
        raise SlicerError(f"нет таких настроек в профилях: {unknown}")
    for key, value in overrides.items():
        target = process if key in process else filament if key in filament else machine
        # Форма — как у профиля: у пластика и машины значения бывают списками.
        if isinstance(target[key], list) and not isinstance(value, list):
            value = [value]
        target[key] = value
    if choice.bed_type:
        process["curr_bed_type"] = choice.bed_type

    # Слайсер получает профили списком через «;» — путь с ним разрезался бы.
    for path in (work, model):
        if ";" in str(path):
            raise SlicerError(f"в пути {path} есть «;» — слайсер разрежет по нему список "
                              f"профилей; переименуйте каталог проекта")
    run_dir = work / f"slice-{uuid.uuid4().hex[:12]}"
    run_dir.mkdir(parents=True)
    try:
        files = {}
        for kind, data in (("machine", machine), ("process", process), ("filament", filament)):
            files[kind] = run_dir / f"{kind}.json"
            files[kind].write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        cmd = [*(command or slicer_command()),
               "--load-settings", f"{files['machine']};{files['process']}",
               "--load-filaments", str(files["filament"]),
               "--arrange", "0", "--orient", "0", "--slice", "0",
               "--export-3mf", "project.3mf", "--outputdir", str(run_dir), str(model)]
        done = _run(cmd, run_dir, timeout)
        result = _result(run_dir, done)
        plates = result.get("sliced_plates") or []
        if len(plates) != 1:
            raise SlicerError(f"нарезано столов: {len(plates)}, ждали один")
        cut = int(plates[0].get("triangle_count", -1))
        if cut != triangles:
            raise SlicerError(f"слайсер нарезал {cut} треугольников, а у детали {triangles}")
        gcode = run_dir / "plate_1.gcode"
        project = run_dir / "project.3mf"
        if not gcode.is_file() or not project.is_file():
            raise SlicerError("слайсер сказал «успешно», но файлов нет")
        config = parse_gcode(gcode)
        _verify(config, choice, overrides)
        warnings = tuple(w for w in [str(plates[0].get("warning_message", "")).strip()] if w)
        # Сводка читается ДО перекладки файлов: G-code без граммов или слоёв —
        # не нарезка, и в output/ такой ложиться не должен. Прежде нехватка
        # строки давала nan уже после того, как файлы легли на место.
        try:
            grams = float(config["total filament used [g]"])
            layers = int(float(config["total layers count"]))
        except (KeyError, ValueError) as e:
            raise SlicerError(f"в G-code слайсера нет сводки ({e}): ни граммов, ни числа "
                              f"слоёв — файлы не приняты") from None
        if not math.isfinite(grams) or grams <= 0 or layers <= 0:
            raise SlicerError(f"сводка G-code пуста: {grams} г, слоёв {layers} — слайсер "
                              f"ничего не нарезал; файлы не приняты")
        out_project.parent.mkdir(parents=True, exist_ok=True)
        os.replace(project, out_project)
        os.replace(gcode, out_gcode)
        return Sliced(
            project=out_project, gcode=out_gcode, choice=choice, overrides=overrides,
            time=config.get("estimated printing time (normal mode)", "?"),
            grams=grams, layers=layers,
            triangles=cut, warnings=warnings, config=config)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def _run(cmd: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess:
    """Запустить слайсер своей группой процессов и при таймауте снять её всю.

    `subprocess.run(timeout=)` убивает только прямого потомка, а у владельца
    это обёртка: distrobox → AppImage, и настоящий слайсер остался бы
    работать, пока его рабочий каталог уже удалён.
    """
    import signal

    try:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
    except OSError as e:
        raise SlicerError(f"слайсер не запустился: {e}") from None
    guard = _orphan_guard(proc.pid)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _stop_guard(guard)
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                break
            try:
                proc.wait(timeout=5)
                break
            except subprocess.TimeoutExpired:
                continue
        proc.communicate()
        raise SlicerError(f"слайсер не уложился в {timeout:g} с и остановлен") from None
    _stop_guard(guard)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


#: Сторож слайсера: отдельный процесс в своей сессии. Ждёт, пока жив тот, кто
#: запустил слайсер; тот исчез, а группа слайсера ещё жива — снимает её.
_GUARD = """
import os, signal, sys, time
parent, group = int(sys.argv[1]), int(sys.argv[2])
def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
while alive(parent) and os.getppid() == parent:
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        sys.exit(0)
    time.sleep(0.5)
for sig in (signal.SIGTERM, signal.SIGKILL):
    try:
        os.killpg(group, sig)
    except ProcessLookupError:
        break
    time.sleep(3)
"""


def _orphan_guard(group: int) -> subprocess.Popen | None:
    """Чтобы слайсер не пережил отменённое задание.

    Слайсер запущен своей сессией (иначе по таймауту не снять обёртку
    distrobox вместе с ним), а отмена задания и предел времени снимают группу
    ВОРКЕРА — слайсер оставался сиротой и резал дальше в уже удалённом
    каталоге (аудит 2026-11-21). Обработчик сигнала тут не поможет: воркер
    снимают SIGKILL. Поэтому рядом — сторож в своей сессии; не запустился —
    работаем как прежде, без него.
    """
    import sys

    try:
        return subprocess.Popen([sys.executable, "-I", "-c", _GUARD, str(os.getpid()),
                                 str(group)],
                                # Трубы, а не /dev/null: перила изолированной
                                # операции не дают открыть и его. Своя сессия —
                                # чтобы сторожа не сняли вместе с группой воркера.
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True)
    except OSError:
        return None


def _stop_guard(guard: subprocess.Popen | None) -> None:
    if guard is None:
        return
    try:
        guard.kill()
        guard.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _result(run_dir: Path, done: subprocess.CompletedProcess) -> dict:
    path = run_dir / "result.json"
    tail = (done.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
    if not path.is_file():
        raise SlicerError(f"слайсер завершился с кодом {done.returncode} без отчёта: "
                          + " / ".join(tail))
    result = json.loads(path.read_text(encoding="utf-8"))
    if int(result.get("return_code", -1)) != 0:
        raise SlicerError(f"слайсер отказал: {result.get('error_string', '?')} "
                          f"(код {result.get('return_code')})")
    return result


def _verify(config: dict, choice: Choice, overrides: dict) -> None:
    """Действующий конфиг нарезки — тот ли, что задуман."""
    wanted = {"printer_settings_id": choice.printer, "print_settings_id": choice.process,
              "filament_settings_id": choice.filament, **overrides}
    if choice.bed_type:
        wanted["curr_bed_type"] = choice.bed_type
    # Ключа нет в подвале — значит, слайсер его не применил: это тоже отказ,
    # даже если задумано пустое значение.
    wrong = [f"{k}: {config.get(k, '<нет>')!r} вместо {v!r}"
             for k, v in wanted.items() if k not in config or not _same(config[k], v)]
    if wrong:
        raise SlicerError("нарезано не тем, что задано: " + "; ".join(wrong))
