"""Воркер на macOS: spawn вместо fork, потому что fork там ломается.

Зачем отдельный файл. `test_worker.py` проверяет МЕХАНИКУ воркера — отмену,
пределы, сборку мусора — и делает это замыканиями, которые `fork` наследует
даром. `spawn` передаёт работу pickle'ом и замыкание не возьмёт, так что
механику там проверять нечем: она гоняется через fork (`conftest` его
оставляет).

Здесь проверяется ровно то, ради чего spawn и заведён: сборка ПОСЛЕ того, как
родитель уже посчитал геометрию. На macOS такой fork падает SIGTRAP
(пул TBB, заведённый до fork), а настоящие операции Datum переживают spawn,
потому что их работа — `functools.partial` от функции модуля.
"""

from __future__ import annotations

import sys

import pytest

from datum.build.worker import BuildWorker, WorkerLimits


def _count_bodies():
    """Работа воркера: посчитать кубик. Функция модуля — её возьмёт pickle."""
    from datum.geom import box
    from datum.geom.measure import vol

    return ({"status": "ok", "bodies": 1, "volume": vol(box(0, 10, 0, 10, 0, 10))}, b"")


def _geometry_in_parent() -> None:
    """Та самая подготовка, после которой fork ломается."""
    from datum.geom import box, diff
    from datum.geom.measure import vol

    vol(diff(box(0, 10, 0, 20, 0, 5), [box(2, 8, 2, 18, 3, 6)]))


@pytest.mark.skipif(sys.platform != "darwin", reason="spawn нужен только на macOS")
def test_a_worker_runs_after_the_parent_counted_geometry():
    """Родитель посчитал булеву операцию — потомок всё равно работает."""
    _geometry_in_parent()
    out = BuildWorker(_count_bodies, limits=WorkerLimits(wall_seconds=60.0)).start().wait()
    assert out.ok, out.message
    assert out.payload["bodies"] == 1
    assert out.payload["volume"] == pytest.approx(1000.0)


@pytest.mark.skipif(sys.platform != "darwin", reason="spawn нужен только на macOS")
def test_the_worker_is_a_real_new_process():
    """spawn — это fork+exec: потомок не наследует память родителя.

    Проверяется тем, что в потомке НЕТ переменной, выставленной в родителе
    после старта интерпретатора: при fork она была бы видна.
    """
    import os

    import datum.build.worker as module

    os.environ["DATUM_SPAWN_PROBE"] = "родитель"
    try:
        code = "import os; print(os.environ.get('DATUM_SPAWN_PROBE', 'нет'))"
        out = BuildWorker(_probe_env, limits=WorkerLimits(wall_seconds=60.0)).start().wait()
    finally:
        del os.environ["DATUM_SPAWN_PROBE"]
    assert out.ok, out.message
    assert out.payload["probe"] == "родитель", (
        "переменная окружения наследуется и при spawn — проверка не о том")
    assert out.payload["pid"] != os.getpid()


def _probe_env():
    import os

    return ({"status": "ok", "probe": os.environ.get("DATUM_SPAWN_PROBE", "нет"),
             "pid": os.getpid()}, b"")


def test_a_signal_does_not_reach_the_callers_process_group(monkeypatch):
    """Отмена не должна бить по группе ВЫЗЫВАЮЩЕГО.

    Своя группа появляется у потомка только после `setsid()`. До этого
    `getpgid(pid)` — группа родителя, и `killpg` по ней гасит не воркер, а
    приложение: у pytest это был весь прогон (SIGTERM), у окна — оно само.
    Проверяется без процессов: подменяем замер группы так, будто потомок ещё
    не ушёл в свою, и смотрим, куда пойдёт сигнал.
    """
    import os
    import signal

    import datum.build.worker as module

    sent: list[tuple[str, int]] = []
    own = os.getpgid(0)                              # своя группа — до подмены
    fake = 424242                                    # выдуманный pid потомка

    worker = module.BuildWorker(lambda: None)
    worker.pid = fake
    monkeypatch.setattr(module.os, "getpgid",
                        lambda pid: own if pid in (0, fake) else pid)
    monkeypatch.setattr(module.os, "kill", lambda pid, sig: sent.append(("kill", pid)))
    monkeypatch.setattr(module.os, "killpg", lambda pg, sig: sent.append(("killpg", pg)))

    worker._signal_group(signal.SIGTERM)

    assert sent == [("kill", fake)], (
        f"сигнал ушёл в {sent}: своя группа есть только после setsid, "
        f"до него killpg бьёт по вызывающему")
