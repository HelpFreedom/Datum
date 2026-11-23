"""Нечисла (nan, ±inf) не проходят внутрь — ни аргументом операции, ни входом
проверки, ни её ответом.

Прежде nan не ронял ничего, а давал ЛОЖНЫЙ ответ с кодом 0: сечение в nan —
«пустое», луч из nan — «не встретил поверхности», а проверка зацепа с
`hold_vol: .nan` — «упор 89.8 мм³ ≥ nan — OK» и «ИТОГ: OK».
"""

from __future__ import annotations

import asyncio
import math

import pytest

import datum.tools.ops  # noqa: F401  — объявление операций
from datum.checks.result import CheckResult
from datum.checks.runner import CheckSpec, run_checks
from datum.doc.params import ParamRegistry
from datum.doc.project import Project
from datum.finite import non_finite
from datum.tools.registry import OPS


def test_non_finite_names_the_place():
    assert non_finite(1.0, "x") is None
    assert non_finite([0.0, 1.0, 2.0], "move") is None
    assert non_finite([0.0, math.nan, 2.0], "move") == ("move[1]", "nan")
    assert non_finite({"a": {"b": [math.inf]}}, "o") == ("o.a.b[0]", "inf")
    assert non_finite({"a": -math.inf}, "o", allow_inf=True) is None
    assert non_finite({"a": math.nan}, "o", allow_inf=True) == ("o.a", "nan")
    assert non_finite("nan", "s") is None          # строка — не число; её судит схема
    assert non_finite(True, "b") is None


def _numeric(arg) -> bool:
    text = str(arg.annotation)
    return "float" in text or "dict" in text


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_every_operation_refuses_a_non_number_before_doing_anything(bad):
    """Перебор ВСЕГО реестра: новая операция с числовым аргументом попадает
    сюда сама. Отказ приходит до работы — поэтому остальные аргументы могут
    быть любыми, и проект может не существовать."""
    tried = 0
    for spec in OPS.values():
        for arg in spec.args:
            if not _numeric(arg):
                continue
            base = arg.base_type
            if "list" in str(base):
                value = [1.0, bad, 1.0]
            elif "dict" in str(base):
                value = {"k": bad}
            else:
                value = bad
            out = asyncio.run(spec.fn(**{arg.name: value}))
            assert isinstance(out, dict) and "error" in out, (spec.name, arg.name)
            assert "не число" in out["error"] and arg.name in out["error"], out["error"]
            tried += 1
    assert tried >= 25, "перебор не нашёл числовых аргументов — сломан отбор"


def test_positional_arguments_are_guarded_too():
    out = asyncio.run(OPS["fem_verify"].fn(math.nan))
    assert "не число" in out["error"] and "modulus" in out["error"]


def test_a_finite_call_still_runs():
    out = asyncio.run(OPS["fem_verify"].fn(modulus=-5.0))
    assert "положительным" in out["error"]           # дошло до тела операции


def test_the_module_name_is_the_guarded_function():
    """Воркер передаёт функцию pickle ПО ИМЕНИ: имя в модуле обязано быть тем
    же объектом, что в реестре, иначе изолированный вызов не состоится."""
    import pickle
    import sys

    import datum.tools.ops  # noqa: F401 — регистрирует операции (и ops_sim)

    for spec in OPS.values():
        # pickle ищет функцию в ЕЁ модуле: операции симуляции живут в ops_sim
        module = sys.modules[spec.fn.__module__]
        assert getattr(module, spec.fn.__name__, None) is spec.fn, spec.name
        assert pickle.loads(pickle.dumps(spec.fn)) is spec.fn


# ------------------------------------------------------------ входы проверок
def _with_check(cube, text: str):
    """Проект ровно с этими проверками — без базовых печатности."""
    (cube / "checks" / "checks.yaml").write_text(text, encoding="utf-8")
    toml = cube / "datum.toml"
    if "baseline = false" not in toml.read_text(encoding="utf-8"):
        toml.write_text(toml.read_text(encoding="utf-8") + "\n[checks]\nbaseline = false\n", encoding="utf-8")
    return Project.open(cube).run_checks()


def test_a_nan_threshold_in_checks_yaml_is_an_error_not_a_pass(cube):
    run = _with_check(cube, """
- id: кубик.стенка
  use: min_wall
  args: {part: "@кубик", wall: .nan, samples: 8}
""")
    (r,) = run.results
    assert r.status == "error" and "wall = nan" in r.message, r.message
    assert "упала" not in r.message                 # проверка цела — неверен вход
    assert not run.ok


def test_infinity_still_means_no_limit(cube):
    """`.inf` — законное «без предела»: страж его не трогает."""
    run = _with_check(cube, """
- id: кубик.поддержки
  use: support_volume
  args: {part: "@кубик", max_volume: .inf}
  severity: blocking
""")
    (r,) = run.results
    assert r.status == "pass", r.message


# ------------------------------------------------------------ ответы проверок
def _reg():
    return ParamRegistry.from_dict({"W": {"value": 2.0, "unit": "mm",
                                          "provenance": "chosen", "reason": "t"}})


@pytest.mark.parametrize("field", ["value", "threshold"])
def test_a_pass_with_nan_inside_is_not_a_pass(field):
    numbers = {"value": 1.0, "threshold": 1.0, field: math.nan}

    def lying(p):
        return CheckResult(id="x", title="x", status="pass", message="упор ≥ порога",
                           op=">=", value=numbers["value"], threshold=numbers["threshold"])

    run = run_checks([CheckSpec(id="x", fn=lying)], _reg())
    (r,) = run.results
    assert r.status == "error" and "НЕ ПРОВЕРЕНО" in r.message and "не число" in r.message
    assert not run.ok


def test_an_infinite_value_is_a_legitimate_answer():
    def endless(p):
        return CheckResult(id="x", title="x", status="pass", message="запас бесконечный",
                           op=">=", value=math.inf, threshold=2.0)

    (r,) = run_checks([CheckSpec(id="x", fn=endless)], _reg()).results
    assert r.status == "pass"


def test_a_failing_nan_stays_a_fail():
    """Провал с nan и так не «зелёный» — его не переписываем."""
    def failing(p):
        return CheckResult(id="x", title="x", status="fail", message="плохо",
                           value=math.nan)

    (r,) = run_checks([CheckSpec(id="x", fn=failing)], _reg()).results
    assert r.status == "fail" and r.message == "плохо"



# ------------------------------------------------------------ ответ с nan
def test_an_answer_with_nan_becomes_an_error():
    """Общее правило реестра: «габарит nan×nan мм» — не замер."""
    from datum.tools.registry import op

    @op("_проба_nan", title="проба", summary="Проба стража ответа: число, которое не число.")
    async def lying() -> dict:
        return {"size": [1.0, math.nan], "report": "габарит 1×nan"}

    @op("_проба_inf", title="проба", summary="Проба стража ответа: бесконечность законна.")
    async def endless() -> dict:
        return {"margin": math.inf, "report": "запас без предела"}

    try:
        out = asyncio.run(OPS["_проба_nan"].fn())
        assert "нечисло" in out["error"] and "size[1]" in out["error"]
        assert asyncio.run(OPS["_проба_inf"].fn())["margin"] == math.inf
    finally:
        OPS.pop("_проба_nan", None)
        OPS.pop("_проба_inf", None)


# ------------------------------------------------------------ ложные ответы
def _call(name, **kwargs):
    return asyncio.run(OPS[name].fn(**kwargs))


def test_gap_search_never_reports_zero_for_a_gap_it_did_not_find(latch):
    """search=1e-300 давал «зазор 0.000 мм» у деталей в 0.3 мм друг от друга."""
    for search in (1e-300, 0.0, -1.0):
        assert "search" in _call("measure_parts", project=str(latch), a="крышка",
                                 b="корпус", search=search)["error"]
    near = _call("measure_parts", project=str(latch), a="крышка", b="корпус", search=0.1)
    assert near["gap_beyond_search"] and "не меньше 0.1 мм" in near["report"]
    far = _call("measure_parts", project=str(latch), a="крышка", b="корпус")
    assert not far["gap_beyond_search"] and far["min_gap_mm"] == pytest.approx(0.3, abs=0.01)


def test_probe_refuses_a_limit_that_is_not_a_limit(latch):
    base = dict(project=str(latch), part="крышка", point=[30.0, 20.0, 40.0])
    for bad in (dict(max_dist=0.0), dict(max_dist=-1.0), dict(cone=-1.0), dict(cone=90.0)):
        out = _call("measure_probe", direction=[0.0, 0.0, -1.0], **base, **bad)
        assert out.get("error"), bad
    # Направление огромной длины — то же направление: прежде длина
    # переполнялась, и луч «не встречал поверхности».
    big = _call("measure_probe", direction=[0.0, 0.0, -1e308], **base)
    small = _call("measure_probe", direction=[0.0, 0.0, -1.0], **base)
    assert big["hit"] == small["hit"] and big["thickness_mm"] == small["thickness_mm"]


def test_a_negative_cache_budget_does_not_empty_the_cache(latch):
    """budget_mb=-1 выбрасывал ВЕСЬ кеш, хотя 0 значит «как в datum.toml»."""
    Project.open(latch).build()
    before = _call("cache_stats", project=str(latch))
    assert "бюджет" in _call("cache_gc", project=str(latch), budget_mb=-1)["error"]
    assert _call("cache_stats", project=str(latch))["objects"] == before["objects"] > 0


def test_footprint_tiny_clearance_and_endless_layer_are_the_plain_shadow():
    """GEOS раздувал на 1e-200 в ПУСТОЙ контур, а слой до 1e308 вырождался:
    в обоих случаях тень выходила площадью 0 и габаритом nan."""
    from datum.geom import box
    from datum.geom.footprint import footprint

    body = box(0, 55.4, 0, 35.4, 13.7, 16.7)
    plain = footprint(body).area
    assert plain == pytest.approx(55.4 * 35.4)
    assert footprint(body, clearance=1e-300).area == pytest.approx(plain)
    assert footprint(body, z1=1e308).area == pytest.approx(plain)
    assert footprint(body, z0=-1e308, z1=1e308).area == pytest.approx(plain)
    assert footprint(body, z0=15.0, z1=16.0).area == pytest.approx(plain)
    with pytest.raises(ValueError, match="предмета нет"):
        footprint(body, z0=20.0, z1=30.0)


def test_footprint_refuses_negative_clearance_and_tolerance(latch):
    for bad in (dict(clearance=-0.2), dict(clearance=2000.0), dict(simplify=-1.0)):
        assert _call("footprint", project=str(latch), part="крышка", **bad).get("error"), bad


def test_occupancy_map_refuses_before_any_work(latch):
    base = dict(project=str(latch), part="крышка", plane="xy", at=15.0, u_from=0.0,
                u_to=60.0, v_from=0.0, v_to=40.0)
    for bad, word in ((dict(step=0.0), "больше нуля"), (dict(u_from=70.0), "наоборот"),
                      (dict(step=1e-4), "ячеек")):
        assert word in _call("occupancy_map", **{**base, **bad})["error"], bad
    assert "#" in _call("occupancy_map", **base)["map"]


def test_lengths_beyond_100_m_are_refused_on_every_path(latch):
    """Подстановка снимка шла мимо правил реестра: CASE_H=1e308 собирал корпус
    высотой 1e308, и в историю версий ложился объём nan."""
    from datum.doc.params import ParamError

    reg = Project.open(latch).params
    with pytest.raises(ParamError, match="по модулю больше 100 м"):
        reg.resolve({"CASE_H": 1e308})
    with pytest.raises(ParamError, match="по модулю больше 100 м"):
        reg.resolve({"CASE_H": -2e5})
    assert reg.resolve({"CASE_H": 99_999.0})["CASE_H"] == 99_999.0
    out = _call("snapshot_create", project=str(latch), tag="проба",
                overrides={"CASE_H": 1e308})
    assert "100 м" in out["error"] and not (latch / "snapshots").exists()


def test_modulus_outside_all_materials_is_refused():
    from datum.fem.model import FemError, Material

    for bad in (1e308, 1e-300, 1e9):
        with pytest.raises(FemError, match="проверьте единицы"):
            Material(bad, 0.35)
    assert Material(2000.0, 0.35).modulus == 2000.0
    assert Material(1.2e6, 0.1).modulus == 1.2e6                 # алмаз


def test_voxel_grid_is_capped_with_a_reason():
    from datum.geom import box
    from datum.geom.repair import voxel_remesh

    cube = box(0, 10, 0, 10, 0, 10).to_trimesh()
    with pytest.raises(ValueError, match="ячеек при пределе"):
        voxel_remesh(cube, pitch=0.001)
    with pytest.raises(ValueError, match="крупнее самой модели"):
        voxel_remesh(cube, pitch=1e308)


def test_a_check_without_an_upper_limit_does_not_break_the_run(cube):
    """`support_volume` по умолчанию (max_volume=inf): запас inf/inf был nan, и
    страж ответов обрывал ВЕСЬ check_run одной строкой «в ответе нечисло» —
    регрессия, найденная агентом при проектировании коробки."""
    run = _with_check(cube, """
- id: кубик.поддержки
  use: support_volume
  args: {part: "@кубик"}
- id: кубик.целостность
  use: single_body
  args: {part: "@кубик"}
""")
    assert [r.status for r in run.results] == ["pass", "pass"]
    assert run.results[0].margin is None
    out = asyncio.run(OPS["check_run"].fn(project=str(cube)))
    assert not out.get("error"), out.get("error")
    assert CheckResult(id="x", title="x", status="pass", message="", op="in", value=5.0,
                       threshold=(1.0, math.inf)).margin == pytest.approx(4.0)
