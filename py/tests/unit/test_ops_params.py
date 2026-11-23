"""Правка реестра параметров операциями: новый параметр, поля, набор значений.

Разбор сессий агента: `params/base.yaml` правили из Bash (`cat >>`, `sed -i`)
159 раз за сессию — `param_set` умел только значение. Правка мимо операции
никем не перечитывалась.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from datum.doc.params import ParamRegistry
from datum.tools import ops, ops_params

LATCH = Path(__file__).resolve().parents[3] / "examples" / "latch"


@pytest.fixture()
def latch(tmp_path):
    root = tmp_path / "latch"
    shutil.copytree(LATCH, root, ignore=shutil.ignore_patterns(".datum", "output"))
    ops.clear_project_cache()
    yield root
    ops.clear_project_cache()


def _run(coro):
    return asyncio.run(coro)


def _reg(root: Path) -> ParamRegistry:
    return ParamRegistry.load(root / "params" / "base.yaml")


def _text(root: Path) -> str:
    return (root / "params" / "base.yaml").read_text("utf-8")


# ------------------------------------------------------------------ новый
def test_add_chosen_and_derived_and_the_project_still_opens(latch):
    before = _text(latch)
    out = _run(ops_params.param_add(str(latch), "RIB_T", "chosen", unit="mm", value=1.6,
                                    reason="четыре периметра", after="GAP"))
    assert out["report"].startswith("добавлен RIB_T = 1.6 mm")
    out = _run(ops_params.param_add(str(latch), "RIB_2", "derived", unit="mm",
                                    expr="RIB_T * 2"))
    assert "error" not in out
    reg = _reg(latch)
    assert reg["RIB_T"].reason == "четыре периметра"
    assert abs(reg.resolve()["RIB_2"] - 3.2) < 1e-12
    # Прежний текст цел до символа: комментарии и порядок на месте.
    text = _text(latch)
    for line in before.splitlines():
        assert line in text
    # GAP записан словарём на несколько строк — новая запись встаёт ПОСЛЕ него,
    # перед следующим параметром, а не посреди.
    assert text.index("GAP:") < text.index("RIB_T:") < text.index("LIP_D:")
    info = _run(ops.project_info(str(latch)))
    assert "error" not in info


def test_add_refuses_what_the_registry_refuses_and_writes_nothing(latch):
    before = _text(latch)
    cases = [
        dict(name="GAP", provenance="chosen", unit="mm", value=1.0),           # уже есть
        dict(name="X1", provenance="assumed", unit="mm", value=1.0),           # без диапазона
        dict(name="X2", provenance="assumed", unit="mm", value=9.0, range=[1.0, 2.0]),
        dict(name="X3", provenance="derived", unit="mm", expr="НЕТ_ТАКОГО * 2"),
        dict(name="X4", provenance="derived", unit="mm", expr="WALL * 2", value=3.0),
        dict(name="X5", provenance="chosen", unit="mm"),                       # без значения
        dict(name="плохое имя", provenance="chosen", unit="mm", value=1.0),
        dict(name="X6", provenance="chosen", unit="попугаи", value=1.0),
        dict(name="X7", provenance="chosen", unit="mm", value=1.0, after="НЕТ"),
    ]
    for case in cases:
        name = case.pop("name")
        prov = case.pop("provenance")
        out = _run(ops_params.param_add(str(latch), name, prov, **case))
        assert out.get("error"), (name, out)
        assert _text(latch) == before, f"{name}: отказ обязан ничего не записать"


def test_add_assumed_with_range_and_hint(latch):
    out = _run(ops_params.param_add(str(latch), "SPRING_K", "assumed", unit="N", value=5.0,
                                    range=[2.0, 9.0], measure_hint="безменом",
                                    blocking=True))
    assert "error" not in out
    p = _reg(latch)["SPRING_K"]
    assert p.provenance == "assumed" and p.blocking and p.measure_hint == "безменом"


# ------------------------------------------------------------------ поля
def test_edit_reason_range_and_blocking(latch):
    reg = _reg(latch)
    assert reg["LIP_D"].provenance == "assumed" and reg["LIP_D"].blocking
    out = _run(ops_params.param_edit(str(latch), "LIP_D", range=[0.4, 1.2], blocking=False,
                                     note="после пробной печати"))
    assert "error" not in out, out
    p = _reg(latch)["LIP_D"]
    assert not p.blocking and p.note == "после пробной печати"
    assert [round(v, 6) for v in p.range] == [0.4, 1.2]
    assert abs(p.display - reg["LIP_D"].display) < 1e-12, "значение не тронуто"


def test_edit_refusals_write_nothing(latch):
    before = _text(latch)
    assert _run(ops_params.param_edit(str(latch), "НЕТ", reason="x")).get("error")
    assert "нечего менять" in _run(ops_params.param_edit(str(latch), "LIP_H"))["error"]
    # Формула — только у производного; диапазон, из которого значение выпало, — отказ.
    assert "только у производного" in _run(
        ops_params.param_edit(str(latch), "LIP_H", expr="GAP * 2"))["error"]
    assert _run(ops_params.param_edit(str(latch), "LIP_D", range=[5.0, 6.0])).get("error")
    assert _text(latch) == before


def test_edit_expr_of_a_derived(latch):
    out = _run(ops_params.param_edit(str(latch), "FLOOR", expr="WALL * 3"))
    assert "error" not in out, out
    reg = _reg(latch)
    assert abs(reg.resolve()["FLOOR"] - reg.resolve()["WALL"] * 3) < 1e-9
    before = _text(latch)
    assert _run(ops_params.param_edit(str(latch), "FLOOR", expr="НЕТ * 3")).get("error")
    # Кольцо: WALL через FLOOR через WALL — реестр отказывает, файл цел.
    assert _run(ops_params.param_edit(str(latch), "WALL", expr="FLOOR * 2")).get("error")
    assert _text(latch) == before


# ------------------------------------------------------------------ набор
def test_set_many_is_all_or_nothing(latch):
    before = _text(latch)
    bad = _run(ops_params.param_set_many(str(latch), {"LIP_H": 2.4, "LIP_D": 99.0}))
    assert "ни один параметр не изменён" in bad["error"] and "вне диапазона" in bad["error"]
    assert _text(latch) == before
    assert "производный" in _run(
        ops_params.param_set_many(str(latch), {"LIP_H": 2.4, "WALL": 1.0}))["error"]
    assert _text(latch) == before
    # GAP — словарь на несколько строк, LIP_D — блоком: оба вида записи.
    good = _run(ops_params.param_set_many(str(latch), {"LIP_H": 2.4, "LIP_D": 0.9, "GAP": 0.25}))
    assert good["changed"]["LIP_H"]["after"] == 2.4
    reg = _reg(latch)
    assert reg["LIP_H"].display == 2.4 and reg["LIP_D"].display == 0.9
    assert reg["GAP"].display == 0.25
    assert reg["LIP_D"].provenance == "assumed", "без источника догадка остаётся догадкой"
    assert "изменено параметров: 3" in good["report"]


def test_set_many_empty_and_unknown(latch):
    assert _run(ops_params.param_set_many(str(latch), {})).get("error")
    assert "такого параметра нет" in _run(
        ops_params.param_set_many(str(latch), {"НЕТ": 1.0}))["error"]
