"""`script_run`: короткий расчёт внутри проекта вместо Python через Bash."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from datum.tools import ops, ops_script
from datum.tools.isolate import run_isolated
from datum.tools.registry import OPS

LATCH = Path(__file__).resolve().parents[3] / "examples" / "latch"


@pytest.fixture()
def latch(tmp_path):
    root = tmp_path / "latch"
    shutil.copytree(LATCH, root, ignore=shutil.ignore_patterns(".datum", "output"))
    ops.clear_project_cache()
    yield str(root)
    ops.clear_project_cache()


def _run(project: str, code: str) -> dict:
    return asyncio.run(ops_script.script_run(project, code))


def test_parts_params_and_geometry_are_at_hand(latch):
    out = _run(latch, 'lid = parts("крышка")\n'
                      'lo, hi = measure.bbox(lid)\n'
                      'print("высота", round(float(hi[2] - lo[2]), 3))\n'
                      'print("объём", round(measure.vol(lid), 1), "зазор", P.GAP)\n'
                      'print(type(geom.box(0, 1, 0, 1, 0, 1)).__name__, np.pi > 3)\n')
    assert "error" not in out, out
    lines = out["output"].splitlines()
    assert lines[0] == "высота 3.0"
    assert lines[1].startswith("объём 5883.5 зазор 0.3")
    assert lines[2] == "Solid True"
    assert "GAP" in out["params_read"], "чтение параметров видно"
    assert out["report"] == out["output"]


def test_the_part_is_the_one_from_the_build(latch):
    built = asyncio.run(ops.part_build(latch, parts=["корпус"]))
    volume = built["parts"]["корпус"]["topology"]["volume_mm3"]
    ops.clear_project_cache()
    out = _run(latch, 'print(repr(round(measure.vol(parts("корпус")), 2)))')
    assert float(out["output"]) == pytest.approx(volume, rel=1e-6)


def test_an_error_names_the_line_of_the_snippet_and_keeps_the_output(latch):
    out = _run(latch, 'print("до ошибки")\nx = 1\ny = x / 0\n')
    assert out["error"].startswith("ZeroDivisionError, строка 3")
    assert out["output"] == "до ошибки\n" and "ОШИБКА" in out["report"]
    assert "строка 2" in _run(latch, "a = 1\nb = (\n")["error"]
    assert "несуществующую деталь" in _run(latch, 'parts("нет")')["error"]


def test_empty_long_and_silent_snippets(latch):
    assert _run(latch, "  \n")["error"] == "пустой фрагмент"
    assert "длиннее" in _run(latch, "x = 1\n" * 5000)["error"]
    assert "ничего не напечатал" in _run(latch, "x = 1")["report"]
    flood = _run(latch, 'print("я" * 50000)')
    assert len(flood["output"]) < ops_script.MAX_OUTPUT + 200 and "вывод обрезан" in flood["output"]


def test_it_runs_in_the_isolated_worker_like_every_script_of_the_project(latch):
    spec = OPS["script_run"]
    assert spec.isolate and spec.danger == "safe"
    out = run_isolated(spec.fn, {"project": latch, "code": 'print(sorted(project.parts))'})
    assert out["output"].strip() == "['корпус', 'крышка']"
