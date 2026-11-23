"""Каталог материалов: параметры MAT_* с происхождением и их путь в проверки."""

from __future__ import annotations

from pathlib import Path

import pytest

from datum.doc.materials import CATALOG, CAVEAT, describe, material_params
from datum.doc.params import ParamRegistry
from datum.doc.project import Project


def test_every_value_has_a_source():
    for key, m in CATALOG.items():
        assert m.url.startswith("https://us.elegoo.com/"), key
        for name, body in material_params(key).items():
            assert body.get("ref") or body.get("source"), f"{key}.{name} без источника"


def test_the_owner_s_caveat_travels_with_every_value():
    """Оговорка владельца: числа из интернета, могут быть неточны."""
    assert "из интернета" in CAVEAT and "неточны" in CAVEAT
    for key in CATALOG:
        for name, body in material_params(key).items():
            assert CAVEAT in (body.get("ref") or body.get("source")), f"{key}.{name}"
        assert CAVEAT in describe(key)


def test_the_modulus_is_an_assumption_spanning_both_directions():
    """Решение владельца: модуль при изгибе Elegoo вместо растяжения — допущение."""
    e = material_params("PLA")["MAT_E"]
    assert e["provenance"] == "assumed" and e["value"] == 2993
    assert e["range"] == [2633 - 165, 2993 + 182]
    assert "ПРИ ИЗГИБЕ" in e["reason"]
    assert "MAT_E" not in material_params("TPU"), "у TPU модуля Elegoo не даёт"


def test_no_allowable_strain_is_in_the_catalogue():
    """Решение владельца: надёжного источника нет — предел остаётся в проекте."""
    for key in CATALOG:
        assert not any("STRAIN" in n for n in material_params(key))


def test_an_unknown_material_gives_nothing():
    assert material_params("PA12") == {}
    assert "нет" in describe("PA12")


def _project(tmp_path: Path, material: str, own: str = "") -> Path:
    root = tmp_path / "п"
    for sub in ("parts", "params", "checks"):
        (root / sub).mkdir(parents=True)
    (root / "datum.toml").write_text(f'name = "п"\n[printer]\nmaterial = "{material}"\n'
                                     '[checks]\nbaseline = false\n',
                                     encoding="utf-8")
    (root / "params" / "base.yaml").write_text(
        "L: {value: 15, unit: mm, provenance: chosen, reason: '—'}\n" + own, encoding="utf-8")
    (root / "parts" / "p.py").write_text(
        "from datum.build import P, part\nfrom datum.geom import box\n\n"
        "@part('брусок')\ndef build():\n    return box(0, P.L, 0, 5, 0, 2)\n", encoding="utf-8")
    (root / "checks" / "checks.yaml").write_text(
        "- id: брусок.масса\n  use: mass_estimate\n  args: {part: '@брусок'}\n",
        encoding="utf-8")
    return root


def test_the_project_gets_the_material_and_the_checks_use_it(tmp_path):
    pr = Project.open(_project(tmp_path, "PETG"))
    assert pr.params["MAT_DENSITY"].value == pytest.approx(1.27)
    run = pr.run_checks()
    (mass,) = run.results
    assert "1.27 г/см³" in mass.message, mass.message
    assert "MAT_DENSITY" in mass.depends_on


def test_the_project_s_own_value_beats_the_catalogue(tmp_path):
    own = "MAT_DENSITY: {value: 1.31, unit: g/cm3, provenance: measured, source: 'весы'}\n"
    pr = Project.open(_project(tmp_path, "PLA", own))
    assert pr.params["MAT_DENSITY"].provenance == "measured"
    assert "1.31 г/см³" in pr.run_checks().results[0].message


def test_with_defaults_adds_only_what_is_missing():
    reg = ParamRegistry.loads("MAT_E: {value: 3000, unit: MPa, provenance: measured, source: 'стенд'}\n")
    out = reg.with_defaults(material_params("PLA"))
    assert out["MAT_E"].provenance == "measured" and "MAT_DENSITY" in out


async def test_param_set_records_a_measured_material_property(tmp_path):
    """Навык велит вносить замер через param_set — для MAT_* это работало «нет»."""
    from datum.tools.ops import clear_project_cache
    from datum.tools.registry import OPS

    root = _project(tmp_path, "PLA")
    refused = await OPS["param_set"].fn(project=str(root), name="MAT_E", value=3100.0)
    assert "укажите source" in refused["error"]
    out = await OPS["param_set"].fn(project=str(root), name="MAT_E", value=3100.0,
                                    source="изгиб образца, стенд")
    assert not out.get("error"), out.get("error")
    clear_project_cache()
    pr = Project.open(root)
    assert pr.params["MAT_E"].provenance == "measured"
    assert pr.params["MAT_E"].value == pytest.approx(3100.0)
    assert "L:" in (root / "params" / "base.yaml").read_text(encoding="utf-8"), "файл цел"
