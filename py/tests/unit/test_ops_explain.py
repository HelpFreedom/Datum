"""«Почему красное» и подбор параметра под проверку."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from datum.doc.params import ParamRegistry
from datum.tools import ops, ops_explain

LATCH = Path(__file__).resolve().parents[3] / "examples" / "latch"
CHECK = "крышка.зацеп.+Z"


@pytest.fixture()
def broken(tmp_path):
    """latch с губкой в размер зазора: зацеп не держит."""
    root = tmp_path / "latch"
    shutil.copytree(LATCH, root, ignore=shutil.ignore_patterns(".datum", "output"))
    params = root / "params" / "base.yaml"
    text = params.read_text("utf-8")
    assert "value: 0.8" in text
    params.write_text(text.replace("value: 0.8", "value: 0.3", 1), encoding="utf-8")
    ops.clear_project_cache()
    yield root
    ops.clear_project_cache()


def _run(coro):
    return asyncio.run(coro)


def test_explain_names_the_parameters_that_decide(broken):
    before = (broken / "params" / "base.yaml").read_text("utf-8")
    out = _run(ops_explain.check_explain(str(broken), CHECK))
    assert out["status"] == "fail"
    rows = {r["param"]: r for r in out["rows"]}
    assert rows["LIP_D"]["flips"] and rows["GAP"]["flips"], "губка и зазор решают исход"
    assert not rows["CASE_H"]["flips"] and not rows["LID_T"]["flips"]
    assert [r["param"] for r in out["rows"]][:2] == sorted(["LIP_D", "GAP"],
                                                           key=lambda n: -rows[n]["effect"])
    assert "МЕНЯЕТ ВЕРДИКТ" in out["report"] and "param_solve" in out["report"]
    assert "производные" in out["report"] and "WALL" in out["report"]
    # Угаданный параметр не выходит из своего диапазона: вниз от 0.3 идти некуда.
    assert rows["LIP_D"]["down"] is None and rows["LIP_D"]["up"]["status"] == "pass"
    assert (broken / "params" / "base.yaml").read_text("utf-8") == before, "ничего не записано"


def test_solve_finds_the_nearest_passing_value_and_writes_nothing(broken):
    before = (broken / "params" / "base.yaml").read_text("utf-8")
    out = _run(ops_explain.param_solve(str(broken), CHECK, "LIP_D"))
    assert out["passes"] and 0.3 < out["value"] < 0.5, out["report"]
    assert (broken / "params" / "base.yaml").read_text("utf-8") == before
    # Найденное действительно проходит, а чуть меньше — нет: это граница.
    ops.clear_project_cache()
    _run(ops.param_set(str(broken), "LIP_D", out["value"]))
    ops.clear_project_cache()
    run = _run(ops.check_run(str(broken), only=[CHECK]))
    assert run["ok"], run["report"]
    assert "может сломать другую" in out["report"]
    assert ParamRegistry.load(broken / "params" / "base.yaml")["LIP_D"].display == out["value"]


def test_solve_by_the_other_parameter_goes_down(broken):
    out = _run(ops_explain.param_solve(str(broken), CHECK, "GAP", lo=0.05, hi=0.3))
    assert out["passes"] and out["value"] < 0.3


def test_solve_refusals(broken):
    assert "не читает" in _run(ops_explain.param_solve(str(broken), CHECK, "E_PLA"))["error"]
    assert "производный" in _run(ops_explain.param_solve(str(broken), CHECK, "WALL"))["error"]
    assert "нет" in _run(ops_explain.param_solve(str(broken), CHECK, "НЕТ"))["error"]
    out = _run(ops_explain.param_solve(str(broken), CHECK, "LIP_D", lo=0.1, hi=5.0))
    assert "менять саму догадку" in out["error"]
    stuck = _run(ops_explain.param_solve(str(broken), CHECK, "CASE_H", lo=18, hi=25))
    assert stuck["passes"] is False and stuck["value"] is None
    assert "этим параметром дело не решить" in stuck["report"]


def test_solve_on_a_passing_check_has_nothing_to_do(tmp_path):
    root = tmp_path / "latch"
    shutil.copytree(LATCH, root, ignore=shutil.ignore_patterns(".datum", "output"))
    ops.clear_project_cache()
    out = _run(ops_explain.param_solve(str(root), CHECK, "LIP_D"))
    assert out["passes"] and "подбирать нечего" in out["report"]
    assert "step" in _run(ops_explain.check_explain(str(root), CHECK, step=5.0))["error"]
