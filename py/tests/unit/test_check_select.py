"""Выборочный прогон проверок, память о прошлом прогоне, вид ответа для агента.

Разбор сессий агента: `check_run` без фильтра вызывали десятки раз подряд, и
после каждого шёл свой Python «покажи, что не прошло и что изменилось».
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from datum.checks.result import CheckResult, CheckRun
from datum.doc import lastrun
from datum.tools import ops
from datum.tools.agent_reply import for_agent

LATCH = Path(__file__).resolve().parents[3] / "examples" / "latch"


@pytest.fixture()
def latch(tmp_path):
    root = tmp_path / "latch"
    shutil.copytree(LATCH, root, ignore=shutil.ignore_patterns(".datum", "output"))
    ops.clear_project_cache()
    yield str(root)
    ops.clear_project_cache()


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ выбор
def test_only_runs_the_named_check_and_says_it_is_not_the_verdict(latch):
    full = _run(ops.check_run(latch))
    one = _run(ops.check_run(latch, only=["крышка.стенка"]))
    assert [r["id"] for r in one["results"]] == ["крышка.стенка"]
    assert len(full["results"]) > 1
    assert one["report"].startswith("ВЫБОРОЧНЫЙ прогон")
    assert "НЕ вердикт проекта" in one["report"]
    assert "ИТОГ ВЫБРАННОГО: OK" in one["report"] and "\nИТОГ: " not in one["report"]
    assert one["unread_params"] is None, "по выборочному прогону о параметрах не судят"


def test_only_takes_patterns_and_result_ids(latch):
    by_pattern = _run(ops.check_run(latch, only=["крышка.*"]))
    ids = [r["id"] for r in by_pattern["results"]]
    assert ids and all(i.startswith("крышка.") for i in ids)
    # id результата длиннее id объявления: «крышка.зацеп.+Z» у «крышка.зацеп».
    one_dir = _run(ops.check_run(latch, only=["крышка.зацеп.+Z"]))
    assert [r["id"] for r in one_dir["results"]] == ["крышка.зацеп.+Z"]


def test_parts_and_group_narrow_the_run(latch):
    out = _run(ops.check_run(latch, parts=["крышка"], group="printing"))
    assert out["results"] and all(r["group"] == "printing" and "крышка" in r["inputs"]
                                  for r in out["results"])
    whole = _run(ops.check_run(latch, parts=["корпус"]))
    assert all("корпус" in r["inputs"] or not r["inputs"] for r in whole["results"])
    # Проверка-формула деталей не называет — под фильтр по детали она остаётся.
    assert any(not r["inputs"] for r in whole["results"])


def test_unknown_names_are_refused_not_run_empty(latch):
    assert "ни одна проверка не подходит" in _run(ops.check_run(latch, only=["нет"]))["error"]
    assert "нет таких деталей" in _run(ops.check_run(latch, parts=["нет"]))["error"]
    assert "нет группы" in _run(ops.check_run(latch, group="нет"))["error"]


# ------------------------------------------------------------ прошлый прогон
def _result(cid: str, status: str) -> CheckResult:
    return CheckResult(id=cid, title=cid, group="g", status=status, message=cid)


def test_changes_name_what_broke_and_what_was_fixed(tmp_path):
    first = CheckRun((_result("a", "pass"), _result("b", "fail"), _result("c", "pass")))
    assert lastrun.changes(tmp_path, first)["known"] is False
    lastrun.remember(tmp_path, first, full=True)
    second = CheckRun((_result("a", "fail"), _result("b", "pass"), _result("c", "pass"),
                       _result("d", "pass")))
    delta = lastrun.changes(tmp_path, second)
    assert delta["worse"] == ["a (прошла → FAIL)"]
    assert delta["better"] == ["b (FAIL → прошла)"]
    assert delta["new"] == ["d"]
    assert any("стало хуже" in line for line in delta["lines"])


def test_selective_run_updates_only_what_it_ran(tmp_path):
    lastrun.remember(tmp_path, CheckRun((_result("a", "pass"), _result("b", "fail"))), full=True)
    lastrun.remember(tmp_path, CheckRun((_result("a", "fail"),)), full=False)
    kept = json.loads((tmp_path / ".datum" / lastrun.FILE).read_text("utf-8"))["checks"]
    assert kept["a"]["status"] == "fail" and kept["b"]["status"] == "fail"
    # Полный прогон заменяет запись целиком: исчезнувшая проверка уходит.
    lastrun.remember(tmp_path, CheckRun((_result("a", "pass"),)), full=True)
    kept = json.loads((tmp_path / ".datum" / lastrun.FILE).read_text("utf-8"))["checks"]
    assert set(kept) == {"a"}


def test_broken_memory_file_is_not_an_error(tmp_path):
    (tmp_path / ".datum").mkdir()
    (tmp_path / ".datum" / lastrun.FILE).write_text("{не json", encoding="utf-8")
    assert lastrun.changes(tmp_path, CheckRun((_result("a", "pass"),)))["lines"] == []


def test_check_run_reports_changes_between_runs(latch):
    first = _run(ops.check_run(latch))
    assert "С прошлого прогона" not in first["report"]
    again = _run(ops.check_run(latch))
    assert "С прошлого прогона вердикты не изменились" in again["report"]
    # Порог стенки крышки — шире самой крышки: проверка обязана покраснеть,
    # и прогон обязан назвать её сам.
    checks = Path(latch) / "checks" / "checks.yaml"
    text = checks.read_text("utf-8")
    assert 'wall: "${params.WALL}", samples: 12' in text
    checks.write_text(text.replace('wall: "${params.WALL}", samples: 12',
                                   "wall: 40.0, samples: 12"), encoding="utf-8")
    ops.clear_project_cache()
    broken = _run(ops.check_run(latch))
    assert broken["changes"]["worse"] and "крышка.стенка" in broken["changes"]["worse"][0]
    assert "стало хуже: крышка.стенка" in broken["report"]


# --------------------------------------------------------------- каталог
def test_check_list_filters_by_name_and_group():
    whole = _run(ops.check_list())
    one = _run(ops.check_list(names=["escape"]))
    assert set(one["checks"]) == {"escape"} and len(whole["checks"]) > 30
    assert "принимает на веру" in one["report"]
    sim = _run(ops.check_list(group="sim"))
    assert sim["checks"] and all(c["group"] == "sim" for c in sim["checks"].values())
    assert set(_run(ops.check_list(names=["sim_*"]))["checks"]) == set(sim["checks"])
    assert "нет таких проверок" in _run(ops.check_list(names=["нет_такой"]))["error"]
    assert "нет группы" in _run(ops.check_list(group="нет"))["error"]


def test_params_list_match(latch):
    out = _run(ops.params_list(latch, match=["LIP_*", "GAP"]))
    assert "GAP" in out["params"] and all(n == "GAP" or n.startswith("LIP_")
                                          for n in out["params"])
    assert out["report"].startswith("Выбрано параметров")
    assert "ни один параметр не подходит" in _run(ops.params_list(latch, match=["НЕТ*"]))["error"]


# ------------------------------------------------------------ вид для агента
def test_agent_view_keeps_everything_that_did_not_pass(latch):
    checks = Path(latch) / "checks" / "checks.yaml"
    checks.write_text(checks.read_text("utf-8").replace(
        'wall: "${params.WALL}", samples: 12', "wall: 40.0, samples: 12"), encoding="utf-8")
    ops.clear_project_cache()
    full = _run(ops.check_run(latch))
    seen = for_agent("check_run", full, {})
    failed = [r for r in full["results"] if r["status"] != "pass"]
    assert failed and [r["id"] for r in seen["results"]] == [r["id"] for r in failed]
    for kept, was in zip(seen["results"], failed):
        # Всё, по чему решают, на месте: число, порог, запас, что читала.
        for key in ("message", "value", "threshold", "margin", "depends_on", "inputs"):
            assert kept[key] == was[key]
    assert seen["results_passed"] == len(full["results"]) - len(failed)
    assert seen["report"] == full["report"], "отчёт — построчно и целиком"
    assert "check_run(only=[id])" in seen["agent_note"]
    # С `only` — проверка полями целиком, прошла она или нет.
    one = _run(ops.check_run(latch, only=["крышка.целостность"]))
    assert for_agent("check_run", one, {"only": ["крышка.целостность"]})["results"] \
        == [{**r} for r in one["results"]]


def test_agent_view_of_build_keeps_numbers_drops_window_fields(latch):
    full = _run(ops.part_build(latch))
    seen = for_agent("part_build", full, {})
    assert "events" not in seen and seen["report"] == full["report"]
    for name, part in seen["parts"].items():
        was = full["parts"][name]
        assert "view" not in part and part["bbox"] == was["view"]["bbox"]
        assert part["topology"] == was["topology"]
        assert part["depends_on"] == was["depends_on"], "деталей мало — список целиком"
        assert "placement" not in part, "деталь не сдвинута — положения в ответе нет"


def test_agent_view_keeps_a_moved_parts_placement_and_counts_reads_of_many():
    item = {"ok": True, "depends_on": ["A", "B"], "view": {"bbox": [0, 0, 0, 1, 1, 1], "url": "/obj/x"},
            "placement": {"move": [5, 0, 0], "rotate": [0, 0, 0], "centre": [0, 0, 0]}}
    many = for_agent("part_build", {"parts": {f"p{i}": dict(item) for i in range(4)},
                                    "report": "…"}, {})
    part = many["parts"]["p0"]
    assert part["placement"]["move"] == [5, 0, 0]
    assert part["depends_on_n"] == 2 and "depends_on" not in part
    assert "part_build(parts=[деталь])" in many["agent_note"]
    # Несобранная деталь — со списком: по нему ищут причину.
    broken = for_agent("part_build", {"parts": {f"p{i}": {**item, "ok": False}
                                                for i in range(4)}, "report": "…"}, {})
    assert broken["parts"]["p0"]["depends_on"] == ["A", "B"]


def test_agent_view_drops_urls_next_to_paths_and_keeps_errors_whole():
    out = for_agent("part_view", {"image": {"url": "/obj/a.png", "path": ".datum/a.png"},
                                  "report": "вид"}, {})
    assert out["image"] == {"path": ".datum/a.png"}
    error = {"error": "нет детали", "report": "ОШИБКА: нет детали", "url": "x"}
    assert for_agent("part_view", error, {}) is error
    assert "checks" not in for_agent("check_list", {"checks": {"a": {}}, "report": "a"}, {})
