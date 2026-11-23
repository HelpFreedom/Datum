"""Перила для скриптов проекта: запрет импорта и запись мимо проекта.

Это НЕ граница безопасности (см. заголовок datum/build/sandbox.py), и тесты
здесь проверяют не неприступность, а то, что обычный промах ловится сразу и
объясняется — и что обычная работа при этом не ломается.

Каждый случай прогоняется через НАСТОЯЩИЙ дочерний процесс: перила живут только
там, и проверять их подменой в текущем процессе значило бы проверять не то.
"""

from __future__ import annotations

import pathlib
import shutil

import pytest

from datum.build.sandbox import (ALLOWED_IMPORTS, DENIED_IMPORTS, ImportDenied,
                                 WriteDenied)
from datum.build.worker import BuildWorker, WorkerLimits, limits_for


# --------------------------------------------------------------- проект-проба
@pytest.fixture
def project(latch, tmp_path):
    """Копия примера, которую не жалко: сюда кладутся вредные детали."""
    dst = tmp_path / "проект"
    shutil.copytree(latch, dst, ignore=shutil.ignore_patterns(
        ".datum", "snapshots", "output", "__pycache__"))
    return dst


def build(project, name: str, source: str) -> dict:
    """Положить деталь в проект и собрать её в дочернем процессе."""
    (project / "parts" / f"{name}.py").write_text(source, encoding="utf-8")

    def body():
        import asyncio

        from datum.tools.ops import clear_project_cache, part_build

        clear_project_cache()
        return ({"status": "ok",
                 "result": asyncio.run(part_build(project=str(project)))}, b"")

    limits = limits_for({"project": str(project)}, WorkerLimits())
    outcome = BuildWorker(body, limits=limits).start().wait()
    assert outcome.ok, outcome.message
    return outcome.payload["result"]


def part_of(result: dict, name: str) -> dict:
    return result["parts"][name]


@pytest.fixture
def outside(tmp_path):
    """Каталог-жертва ВНЕ /tmp.

    `tmp_path` для этого не годится: `/tmp` разрешён намеренно — через него
    работает `tempfile`, и туда же воркер пишет свой результат. Жертва внутри
    разрешённого каталога проверяла бы не перила, а собственную невнимательность
    (на этом тесты и провалились в первый раз). Каталог убирается всегда, в том
    числе после падения, — иначе неудачный прогон оставлял бы мусор в доме."""
    path = pathlib.Path.home() / ".datum-проба-перил"
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


# ------------------------------------------------------------------- импорт
def test_forbidden_import_is_refused_with_advice(project):
    """Отказ обязан говорить, ЧЕМ пользоваться вместо. «Нельзя» без объяснения —
    худший вид отказа: агент просто попробует то же самое ещё раз."""
    result = build(project, "промах", """
import os
from datum.build import part
from datum.geom import box

@part("промах")
def build():
    return box(0, 1, 0, 1, 0, 1)
""")
    error = result.get("error") or ""
    assert "ImportDenied" in error and "«os»" in error
    assert "export" in error                      # названа замена


@pytest.mark.parametrize("module", ["os", "sys", "subprocess", "ctypes", "socket"])
def test_the_notorious_modules_are_named(module):
    """У каждого запрещённого есть объяснение, а не пустая строка."""
    assert len(DENIED_IMPORTS[module]) > 30
    assert module not in ALLOWED_IMPORTS


def test_allowed_imports_cover_what_real_scripts_use(project):
    """Список выведен из настоящих скриптов CAD: shapely, trimesh, math, numpy.
    Если он ужмётся так, что обычная деталь перестанет строиться, упадёт это."""
    result = build(project, "обычная", """
import math
import numpy as np
import shapely
import trimesh
from datum.build import part
from datum.geom import box

@part("обычная")
def build():
    return box(0, math.floor(2.7), 0, float(np.array([1.0])[0]), 0, 1)
""")
    assert not result.get("error"), result.get("error")
    assert part_of(result, "обычная")["ok"]


def test_libraries_import_freely(project):
    """Правило касается ТОЛЬКО скриптов проекта. numpy внутри себя импортирует
    и os, и sys; если бы запрет распространялся на библиотеки, не взлетело бы
    вообще ничего."""
    result = build(project, "через_библиотеку", """
from datum.build import part
from datum.geom import box
import trimesh                      # тянет за собой os, sys и многое ещё

@part("через_библиотеку")
def build():
    assert trimesh.creation.box is not None
    return box(0, 1, 0, 1, 0, 1)
""")
    assert not result.get("error"), result.get("error")


# -------------------------------------------------------------------- запись
def test_writing_outside_the_project_is_refused(project, outside):
    """Тот самый промах, ради которого перила и ставятся: скрипт пишет туда,
    куда не должен. Проверяется и сообщение, и ОТСУТСТВИЕ файла."""
    victim = outside / "ИСПОРЧЕНО.txt"
    result = build(project, "чужая_запись", f"""
from pathlib import Path
from datum.build import part
from datum.geom import box

@part("чужая_запись")
def build():
    Path({str(victim)!r}).write_text("тут был агент")
    return box(0, 1, 0, 1, 0, 1)
""")
    # Отказ приходит СТРУКТУРОЙ, а не строкой: интерфейс и агент разбирают
    # `reason`, человеку показывается `message` с виновным путём.
    error = part_of(result, "чужая_запись")["error"]
    assert error["reason"] == "WriteDenied"
    assert str(victim) in error["message"]
    assert not victim.exists(), "перила пропустили запись"


def test_pathlib_is_covered(project, outside):
    """Именной замок. pathlib ходит через `io.open`, а это ОТДЕЛЬНАЯ ссылка на
    тот же объект: подмена `builtins.open` её не трогает. Первая версия перил
    из-за этого пропускала самый обычный способ записать файл."""
    victim = outside / "мимо.txt"
    build(project, "через_pathlib", f"""
from pathlib import Path
from datum.build import part
from datum.geom import box

@part("через_pathlib")
def build():
    Path({str(victim)!r}).write_text("x")
    return box(0, 1, 0, 1, 0, 1)
""")
    assert not victim.exists()


def test_plain_open_is_covered(project, outside):
    victim = outside / "мимо2.txt"
    build(project, "через_open", f"""
from datum.build import part
from datum.geom import box

@part("через_open")
def build():
    with open({str(victim)!r}, "w") as f:
        f.write("x")
    return box(0, 1, 0, 1, 0, 1)
""")
    assert not victim.exists()


def test_deleting_outside_the_project_is_refused(project, outside):
    """Запись — половина беды; удаление чужого файла тоже должно отвергаться."""
    victim = outside / "ценное.txt"
    victim.write_text("не трогать", encoding="utf-8")
    build(project, "удаление", f"""
import shutil
from pathlib import Path
from datum.build import part
from datum.geom import box

@part("удаление")
def build():
    Path({str(victim)!r}).unlink()
    return box(0, 1, 0, 1, 0, 1)
""")
    assert victim.exists() and victim.read_text(encoding="utf-8") == "не трогать"


def test_writing_into_the_project_is_allowed(project):
    """Перила не должны мешать обычной работе: проект — своя территория."""
    result = build(project, "своя_запись", """
from pathlib import Path
from datum.build import part
from datum.geom import box

@part("своя_запись")
def build():
    Path(__file__).parent.parent.joinpath("заметка.txt").write_text("ок")
    return box(0, 1, 0, 1, 0, 1)
""")
    assert not result.get("error"), result.get("error")
    assert (project / "заметка.txt").read_text(encoding="utf-8") == "ок"


def test_temporary_files_are_allowed(project):
    """Временные файлы нужны и самому воркеру: он пишет туда свой результат.
    Запрети их — не вернётся ни одна сборка."""
    result = build(project, "временный", """
import tempfile
from pathlib import Path
from datum.build import part
from datum.geom import box

@part("временный")
def build():
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("черновик")
    return box(0, 1, 0, 1, 0, 1)
""")
    assert not result.get("error"), result.get("error")


# ------------------------------------------------- перила на всех поверхностях
def test_every_surface_gets_the_guard_rails():
    """Перила выводятся из аргументов операции, а поверхностей три: CLI, MCP и
    сервер. Вывод живёт в одном `limits_for` именно потому, что, будучи
    повторённым, однажды разошёлся: в CLI перила стояли, а на сервере — нет.
    Здесь закреплено, что повторов больше нет."""
    import inspect

    from datum.server import jobs
    from datum.tools import isolate

    for module in (isolate, jobs):
        source = inspect.getsource(module)
        assert "limits_for(" in source, module.__name__
        assert "project_dir=" not in source, (
            f"{module.__name__} выводит каталог проекта сам — так он и разойдётся")

    base = WorkerLimits()
    assert limits_for({"project": "/tmp/п"}, base).project_dir == "/tmp/п"
    assert limits_for({}, base).project_dir == ""       # операции без проекта
    fixed = WorkerLimits(project_dir="/явно")
    assert limits_for({"project": "/другое"}, fixed).project_dir == "/явно"


def test_an_operation_can_ask_for_more_memory_on_every_surface():
    """Чужой меш весит, сколько весит файл: операции над ним объявляют запас
    памяти сами. Запас только РАСШИРЯЕТ умолчание и не трогает явный потолок,
    и доходит он через тот же `limits_for` на всех трёх поверхностях (MCP,
    сервер, CLI) — иначе на одной модель падала бы, а на другой проходила."""
    import inspect

    from datum.server import jobs
    from datum.tools import cli_adapter, isolate, ops  # noqa: F401 — регистрирует операции
    from datum.tools.registry import OPS

    base = WorkerLimits()
    assert limits_for({}, base, headroom_mb=4096).headroom_mb == 4096
    assert limits_for({}, base, headroom_mb=100).headroom_mb == base.headroom_mb
    fixed = WorkerLimits(memory_mb=3000)
    assert limits_for({}, fixed, headroom_mb=4096).memory_limit_mb() == 3000

    for name in ("mesh_diagnose", "mesh_repair", "import_model", "fem_solve", "slice"):
        assert (OPS[name].memory_headroom_mb or 0) >= 4096, name
    # CLI — третья поверхность: однажды забытая, `datum fem` получал 1 ГБ вместо 4.
    for module in (isolate, jobs, cli_adapter):
        assert "headroom_mb=spec.memory_headroom_mb" in inspect.getsource(module), (
            f"{module.__name__} не передаёт запас операции")


def test_the_guard_is_actually_installed_in_the_child():
    """Прямая проверка: без каталога проекта перил нет, с ним — есть."""

    def body():
        import builtins
        import io
        import os

        return ({"status": "ok", "result": {
            "open": builtins.open.__name__,
            "io.open": io.open.__name__,
            "os.open": os.open.__name__,
            "__import__": builtins.__import__.__name__,
        }}, b"")

    without = BuildWorker(body, limits=WorkerLimits()).start().wait()
    assert set(without.payload["result"].values()) == {"open", "__import__"}

    with_rails = BuildWorker(
        body, limits=WorkerLimits(project_dir="/tmp/п")).start().wait()
    marks = with_rails.payload["result"]
    assert marks["open"] == "guarded_open"
    assert marks["io.open"] == "guarded_open"          # мина из pathlib
    assert marks["os.open"] == "guarded_os_open"
    assert marks["__import__"] == "guarded"


def test_errors_are_named_types():
    """Отказы — не безликий Exception: и агент, и интерфейс их различают."""
    assert issubclass(ImportDenied, ImportError)
    assert issubclass(WriteDenied, PermissionError)
