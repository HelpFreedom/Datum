"""Сборка в отдельном процессе: пределы, отмена, падения.

Смысл модуля — чтобы беда в скрипте детали не уносила с собой приложение.
Каждая гарантия проверяется настоящим процессом, а не заглушкой.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest

from datum.build.worker import (BuildWorker, LimitTooLow, WorkerLimits, _mb,
                                current_vsize_mb, run_in_worker, stage)

FAST = WorkerLimits(wall_seconds=20.0, grace_seconds=0.5)


# ------------------------------------------------------------------ обычный ход
def _ok():
    return ({"status": "ok", "answer": 42}, b"\x01\x02")


def test_result_comes_back():
    out = run_in_worker(_ok, limits=FAST)
    assert out.ok and out.payload["answer"] == 42
    assert out.mesh == b"\x01\x02"
    assert out.ms > 0


@pytest.mark.skipif(sys.platform != "linux", reason="свойство процесса Linux")
def test_only_the_worker_disables_core_dumps():
    import ctypes

    PR_GET_DUMPABLE = 3
    libc = ctypes.CDLL(None)
    parent = libc.prctl(PR_GET_DUMPABLE, 0, 0, 0, 0)

    def job():
        return {"dumpable": libc.prctl(PR_GET_DUMPABLE, 0, 0, 0, 0)}, b""

    out = run_in_worker(job, limits=FAST)
    assert out.ok and out.payload["dumpable"] == 0
    assert libc.prctl(PR_GET_DUMPABLE, 0, 0, 0, 0) == parent


def test_arguments_are_passed():
    def job(a, b=0):
        return ({"status": "ok", "sum": a + b}, b"")

    out = run_in_worker(job, 3, b=4, limits=FAST)
    assert out.payload["sum"] == 7


def test_exception_becomes_a_result_not_a_crash():
    def job():
        raise ValueError("так вышло")

    out = run_in_worker(job, limits=FAST)
    assert out.status == "error"
    assert "ValueError" in out.message and "так вышло" in out.message


# ------------------------------------------------------------------ пределы
def test_memory_limit_is_enforced():
    """Предел берётся с запасом над уже занятым — иначе сорвётся сам запуск."""
    def job():
        blob = bytearray(8_000_000_000)      # восемь гигабайт при пределе много меньше
        return ({"status": "ok", "n": len(blob)}, b"")

    limit = int(current_vsize_mb()) + 512
    out = run_in_worker(job, limits=WorkerLimits(memory_mb=limit, wall_seconds=30.0))
    assert out.status in ("error", "killed"), out
    assert "памят" in out.message.lower() or "MemoryError" in out.message


def test_cpu_limit_is_enforced():
    def job():
        while True:                          # чистый счёт, без системных вызовов
            sum(i * i for i in range(10_000))

    t0 = time.perf_counter()
    out = run_in_worker(job, limits=WorkerLimits(cpu_seconds=1, wall_seconds=30.0,
                                                 grace_seconds=0.5))
    took = time.perf_counter() - t0
    assert out.status in ("killed", "error"), out
    assert took < 20.0, "предел процессорного времени должен сработать быстро"


def test_the_refusal_names_the_stage_that_ate_the_time():
    """«Исчерпан предел» без этапа отправлял гадать: сборка это или проверка."""
    def job():
        with stage("проверка подставка.стенки"):
            with stage("сборка детали подставка"):
                pass                         # вложенный этап кончился
            while True:
                sum(i * i for i in range(10_000))

    out = run_in_worker(job, limits=WorkerLimits(cpu_seconds=1, wall_seconds=30.0,
                                                 grace_seconds=0.5))
    assert out.status == "killed", out
    assert "SIGXCPU" in out.message
    assert "этап: проверка подставка.стенки," in out.message, out.message
    assert "сборка детали" not in out.message, "вышедший этап не должен остаться"


def test_stage_outside_a_worker_does_nothing():
    with stage("вне воркера"):
        pass


def test_wall_clock_limit_is_enforced():
    def job():
        time.sleep(60)                       # процессорное время не тратится

    t0 = time.perf_counter()
    out = run_in_worker(job, limits=WorkerLimits(wall_seconds=1.0, grace_seconds=0.5))
    assert out.status == "timeout"
    assert "не уложилась" in out.message
    assert time.perf_counter() - t0 < 10.0


def test_outbound_network_is_blocked_but_local_sockets_work():
    """Запрещать сокеты целиком нельзя.

    Первая версия подменяла `socket.socket`, и asyncio падал ещё до начала
    работы: он создаёт себе локальную пару сокетов для пробуждения цикла.
    Запрещается именно выход наружу.
    """
    def job():
        import asyncio
        import socket

        out = {}

        async def hi():
            return "жив"

        out["asyncio"] = asyncio.run(hi())
        a, b = socket.socketpair()
        a.close(); b.close()
        out["socketpair"] = "ок"
        try:
            socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("1.1.1.1", 80))
            out["outbound"] = "прошёл"
        except PermissionError as e:
            out["outbound"] = f"запрещён: {e}"
        try:
            socket.getaddrinfo("example.com", 80)
            out["dns"] = "прошёл"
        except PermissionError:
            out["dns"] = "запрещено"
        return ({"status": "ok", **out}, b"")

    out = run_in_worker(job, limits=FAST)
    assert out.ok, out
    assert out.payload["asyncio"] == "жив"
    assert out.payload["socketpair"] == "ок"
    assert out.payload["outbound"].startswith("запрещён")
    assert "сеть запрещена" in out.payload["outbound"]
    assert out.payload["dns"] == "запрещено"


def test_network_can_be_allowed():
    def job():
        import socket

        try:
            socket.getaddrinfo("localhost", 80)
            return ({"status": "ok", "dns": "разрешено"}, b"")
        except PermissionError:
            return ({"status": "ok", "dns": "запрещено"}, b"")

    out = run_in_worker(job, limits=WorkerLimits(allow_network=True, wall_seconds=20.0))
    assert out.ok and out.payload["dns"] == "разрешено"


# ------------------------------------------------------------------ падения
def test_hard_crash_is_reported_not_propagated():
    def job():
        os.kill(os.getpid(), signal.SIGSEGV)

    out = run_in_worker(job, limits=FAST)
    assert out.status == "killed"
    assert "ядро" in out.message


def test_a_crash_at_the_memory_limit_is_called_lack_of_memory():
    """Отказанный malloc в native-коде — это SIGSEGV, а не MemoryError.

    Так импорт модели в 1.48 млн треугольников и падал: «упало геометрическое
    ядро», хотя модель была цела, а кончился предел адресного пространства.
    Здесь потомок занимает адреса почти до предела, даёт опросу себя увидеть и
    падает — сообщение обязано говорить о памяти, а не о ядре."""
    limit = int(current_vsize_mb()) + 600

    def job():
        import mmap

        held = mmap.mmap(-1, 520 * 1024 * 1024)   # резерв адресов, без касания
        time.sleep(0.3)
        os.kill(os.getpid(), signal.SIGSEGV)
        return ({"status": "ok", "n": len(held)}, b"")

    out = run_in_worker(job, limits=WorkerLimits(memory_mb=limit, wall_seconds=20.0,
                                                 grace_seconds=0.5))
    assert out.status == "killed"
    assert "не хватило памяти" in out.message, out.message
    assert "ядро" not in out.message
    assert f"при пределе {_mb(limit)} МБ" in out.message    # предел назван числом


def test_a_heavy_parent_does_not_turn_every_crash_into_lack_of_memory():
    """Потомок рождается с адресами родителя. У pytest после сотен тестов их
    4.3 ГБ при пределе 5.3 — и мгновенное падение без единого выделения
    выходило «нехваткой памяти». Судить надо по РОСТУ сверх унаследованного."""
    import mmap

    ballast = mmap.mmap(-1, 3 * 1024 ** 3)   # родитель «разросся»: резерв адресов
    try:
        def job():
            os.kill(os.getpid(), signal.SIGSEGV)

        out = run_in_worker(job, limits=FAST)
    finally:
        ballast.close()
    assert out.status == "killed"
    assert "ядро" in out.message and "памяти" not in out.message, out.message


def test_malloc_arenas_do_not_eat_the_address_space():
    """glibc заводит по арене malloc в 64 МБ адресов на каждый поток, а предел
    потомка — предел адресов. На 16 потоках manifold3d это гигабайт пустых
    резервов, которых хватало, чтобы уронить импорт большой модели."""
    def job():
        import threading

        from datum.build.worker import current_vsize_mb as vsize

        before = vsize()
        stop = time.perf_counter() + 0.5

        def churn():
            while time.perf_counter() < stop:
                blobs = [bytearray(64_000) for _ in range(50)]
                del blobs

        threads = [threading.Thread(target=churn) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return ({"status": "ok", "grew_mb": vsize() - before}, b"")

    out = run_in_worker(job, limits=FAST)
    assert out.ok, out.message
    # Каждый поток ещё держит свой стек (8 МБ адресов) — это честные 128 МБ;
    # арены на 16 потоков дали бы сверху до гигабайта.
    assert out.payload["grew_mb"] < 400, out.payload


def test_silent_exit_is_reported():
    def job():
        os._exit(7)

    out = run_in_worker(job, limits=FAST)
    assert out.status == "killed" and "без результата" in out.message


# ------------------------------------------------------------------ отмена
def test_cancel_stops_a_long_job():
    def job():
        time.sleep(60)

    w = BuildWorker(job, limits=WorkerLimits(wall_seconds=60.0, grace_seconds=0.5)).start()
    time.sleep(0.3)
    t0 = time.perf_counter()
    w.cancel()
    out = w.wait()
    assert out.status == "cancelled"
    assert time.perf_counter() - t0 < 5.0, "отмена не должна ждать долго"


def test_cancel_kills_a_job_that_ignores_sigterm():
    """SIGTERM не прерывает вычисление в C++ — на этот случай есть SIGKILL."""
    def job():
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(60)

    w = BuildWorker(job, limits=WorkerLimits(wall_seconds=60.0, grace_seconds=0.5)).start()
    time.sleep(0.3)
    t0 = time.perf_counter()
    w.cancel()
    out = w.wait()
    assert out.status == "cancelled"
    assert out.exit_code == -signal.SIGKILL, "должен быть добит SIGKILL"
    assert time.perf_counter() - t0 < 5.0


def test_whole_process_group_is_killed():
    """Скрипт мог породить подпроцесс — сирот оставаться не должно."""
    # Внук узнаётся по необычной длительности sleep, а не по комментарию в
    # команде: bash в роли /bin/sh (Arch) исполняет единственную команду
    # напрямую, и `sleep 120 # метка` превращался в голый `sleep 120` —
    # тест не находил внука, хотя тот жил. dash (Debian) держал метку.
    duration = "120.424242"
    marker = r"^sleep 120\.424242$"

    def job():
        subprocess.Popen(["/bin/sh", "-c", f"sleep {duration}"])
        time.sleep(60)

    w = BuildWorker(job, limits=WorkerLimits(wall_seconds=60.0, grace_seconds=0.5)).start()
    time.sleep(0.6)
    alive = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True)
    assert alive.stdout.split(), "подпроцесс должен был запуститься"
    w.cancel()
    w.wait()
    time.sleep(0.4)
    left = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True)
    assert not left.stdout.split(), f"остались сироты: {left.stdout.split()}"


# ------------------------------------------------------------------ чистота
def test_no_temp_files_left_even_when_the_child_is_killed():
    """Потомка могли убить посреди записи — рядом остаётся result.bin.tmp.

    Первая версия уборки удаляла только result.bin, и каталог оставался навсегда.
    """
    def slow():
        time.sleep(30)

    before = {d for d in os.listdir("/tmp") if d.startswith("datum-worker-")}

    for _ in range(3):
        assert run_in_worker(_ok, limits=FAST).ok
    w = BuildWorker(slow, limits=WorkerLimits(wall_seconds=60.0,
                                              grace_seconds=0.3)).start()
    time.sleep(0.2)
    w.cancel()
    assert w.wait().status == "cancelled"
    with pytest.raises(LimitTooLow):
        BuildWorker(_ok, limits=WorkerLimits(memory_mb=64)).start()

    after = {d for d in os.listdir("/tmp") if d.startswith("datum-worker-")}
    assert not (after - before), f"остались временные каталоги: {after - before}"


def test_no_zombies_left():

    out = subprocess.run(["ps", "-o", "stat=", "--ppid", str(os.getpid())],
                         capture_output=True, text=True).stdout
    assert not [l for l in out.split() if l.startswith("Z")], "остались зомби"


# ------------------------------------------------------------------ пол предела
def test_limit_below_the_interpreter_footprint_is_refused():
    """Предел ниже уже занятого — не ограничение, а гарантированный сбой.

    Измерено: numpy, scipy, manifold3d, trimesh и shapely резервируют около
    1475 МБ виртуального адресного пространства только на загрузку, и потомок
    наследует это при fork. Предел 512 МБ убивал процесс на старте с пустым
    MemoryError, и понять причину было нельзя.
    """
    with pytest.raises(LimitTooLow, match="ниже того, что уже занимает"):
        BuildWorker(_ok, limits=WorkerLimits(memory_mb=64)).start()


def test_current_footprint_is_measurable():
    mb = current_vsize_mb()
    assert mb > 10, "должно читаться из /proc/self/status"


def test_realistic_limit_is_accepted():
    need = int(current_vsize_mb()) + 512
    out = run_in_worker(_ok, limits=WorkerLimits(memory_mb=need, wall_seconds=20.0))
    assert out.ok


# ------------------------------------------------- подбираемый предел памяти
def test_the_default_limit_is_headroom_above_the_parent(monkeypatch):
    """Умолчание — ЗАПАС СВЕРХ уже отображённого, а не абсолютный потолок.

    Абсолютные 4096 МБ держались ровно до появления тетраэдрального мешера:
    одна построенная сетка раздувает виртуальное пространство родителя на
    0.9…1.4 ГБ (резервы пулов потоков и арен, а не занятая память), и после
    этого НИ ОДНА изолированная сборка не запускалась. Скрипт детали к этим
    отображениям непричастен — он наследует их даром.
    """
    import datum.build.worker as module

    monkeypatch.setattr(module, "current_vsize_mb", lambda: 8000.0)
    assert WorkerLimits().memory_limit_mb() == 8000 + WorkerLimits().headroom_mb
    # У маленького родителя работает пол.
    monkeypatch.setattr(module, "current_vsize_mb", lambda: 100.0)
    assert WorkerLimits().memory_limit_mb() == module.DEFAULT_MEMORY_MB


def test_an_explicit_limit_stays_a_hard_ceiling(monkeypatch):
    """Подстраивать заданное явно число значило бы молча отменять волю
    вызывающего — и заодно обессмыслить проверку «предел слишком мал»."""
    import datum.build.worker as module

    monkeypatch.setattr(module, "current_vsize_mb", lambda: 8000.0)
    assert WorkerLimits(memory_mb=64).memory_limit_mb() == 64
    with pytest.raises(LimitTooLow):
        BuildWorker(_ok, limits=WorkerLimits(memory_mb=64)).start()


def test_the_limit_is_resolved_at_start_not_at_construction(monkeypatch):
    """Пределы держат в константе модуля, созданной при импорте, когда родитель
    ещё маленький. Подбирать предел тогда — значит подобрать не тот."""
    import datum.build.worker as module

    limits = WorkerLimits(wall_seconds=20.0, grace_seconds=0.5)   # родитель мал
    monkeypatch.setattr(module, "current_vsize_mb", lambda: 6000.0)
    worker = BuildWorker(_ok, limits=limits).start()
    try:
        assert worker.limits.memory_mb == 6000 + limits.headroom_mb
        assert worker.wait().ok
    finally:
        worker.cancel()


# ------------------------------------------------ воркер не переживает родителя
def test_a_worker_dies_with_its_parent(tmp_path):
    """Своя группа процессов отрывала воркер от терминала: после `timeout` или
    `Ctrl-C` командная строка умирала, а сборка жила дальше с ppid 1 и жгла
    процессор до своего предела."""
    import os
    import subprocess
    import sys
    import time

    marker = tmp_path / "pid.txt"
    script = f"""
import os, time
from datum.build.worker import BuildWorker
def forever():
    open({str(marker)!r}, "w").write(str(os.getpid()))
    while True:
        pass
BuildWorker(forever).start()
time.sleep(2.0)
os._exit(0)                      # родитель умирает, не дождавшись
"""
    # Вывод — в никуда, а не в канал: иначе без починки выживший воркер держал
    # бы канал открытым, и тест висел бы до таймаута вместо внятного провала.
    subprocess.run([sys.executable, "-c", script], cwd=os.getcwd(), timeout=60,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pid = int(marker.read_text())
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        with open(f"/proc/{pid}/stat") as f:        # зомби без родителя — тоже конец
            if f.read().split()[2] == "Z":
                break
        time.sleep(0.05)
    else:
        os.kill(pid, 9)
        raise AssertionError("воркер пережил родителя")


def test_the_worker_yields_the_cpu_to_the_human():
    """Расчёт идёт с пониженным приоритетом: окно и браузер не должны вставать,
    пока считается МКЭ или сборка (аудит 2026-11-21)."""
    import os

    from datum.build.worker import WORKER_NICE
    from datum.tools.isolate import run_isolated

    out = run_isolated(_niceness, {})
    assert out["nice"] >= min(19, os.nice(0) + WORKER_NICE)


async def _niceness() -> dict:
    import os

    return {"nice": os.nice(0), "report": "ok"}
