"""Зоны детали: упругий язычок тоньше стенки нарочно — и проверка это знает.

Прежде агент поднимал `max_lost_area` под язычок, и порог переставал ловить
настоящую тонкую стенку где угодно ещё.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from datum.doc.project import Project, ProjectError

PART = '''\
from datum.build import P, part
from datum.geom import box, union


def _tongue():
    return box(20, 20 + P.TONGUE_T, 0, 10, 0, 15)


@part("гнездо", print_up="+Z", zones=lambda: {"язычок": box(19.5, 21.5 + P.TONGUE_T, -1, 11, -1, 16)})
def build():
    # Толстая стенка и тонкий язычок (1 мм при требовании 1.6).
    return union([box(0, 10, 0, 10, 0, 15), box(10, 20, 0, 10, 0, 3), _tongue()])
'''


def _project(tmp_path: Path, checks: str, placement: str = "") -> Path:
    root = tmp_path / "гнездо"
    for sub in ("parts", "params", "checks"):
        (root / sub).mkdir(parents=True)
    (root / "datum.toml").write_text('name = "гнездо"\n', encoding="utf-8")
    (root / "params" / "base.yaml").write_text(
        "TONGUE_T: {value: 1.0, unit: mm, provenance: chosen, reason: 'упругий'}\n",
        encoding="utf-8")
    (root / "parts" / "p.py").write_text(PART, encoding="utf-8")
    (root / "checks" / "checks.yaml").write_text(checks, encoding="utf-8")
    if placement:
        (root / "placement.yaml").write_text(placement, encoding="utf-8")
    return root


WALL = "- id: гнездо.стенки\n  use: min_wall\n  args: {part: '@гнездо', wall: 1.6, max_lost_area: 5.0%s}\n"


def test_the_tongue_fails_the_wall_check_without_its_zone(tmp_path):
    run = Project.open(_project(tmp_path, WALL % "")).run_checks()
    assert run.results[0].status == "fail", run.report()


def test_excluding_the_zone_passes_and_says_what_was_excluded(tmp_path):
    run = Project.open(_project(tmp_path, WALL % ", exclude_zones: [язычок]")).run_checks()
    r = run.results[0]
    assert r.status == "pass", r.message
    assert "без зон язычок" in r.message and "тонкого" in r.message
    assert "TONGUE_T" in r.depends_on, "зона читает параметр — зависимость видна"


def test_the_zone_moves_with_the_part(tmp_path):
    root = _project(tmp_path, WALL % ", exclude_zones: [язычок]",
                    placement="гнездо: {move: [50, 30, 0], rotate: [0, 0, 90]}\n")
    assert Project.open(root).run_checks().results[0].status == "pass"


def test_an_unknown_zone_is_refused_with_the_list(tmp_path):
    run = Project.open(_project(tmp_path, WALL % ", exclude_zones: [защёлка]")).run_checks()
    r = run.results[0]
    assert r.status == "error" and "нет зон ['защёлка']" in r.message and "язычок" in r.message


def test_a_check_without_zones_refuses_them(tmp_path):
    text = "- id: гнездо.тело\n  use: single_body\n  args: {part: '@гнездо', exclude_zones: [язычок]}\n"
    r = Project.open(_project(tmp_path, text)).run_checks().results[0]
    assert r.status == "error" and "зон не исключает" in r.message


def test_overhang_inside_a_zone_is_not_counted(tmp_path):
    from datum.checks.printing import overhang
    from datum.geom import box, union

    # Полка-консоль 10 мм над пустотой: нависание 100 мм².
    shelf = union([box(0, 5, 0, 10, 0, 10), box(5, 15, 0, 10, 8, 10)])
    full = overhang(shelf, max_frac=1.0)
    zone = box(4, 16, -1, 11, 7, 9).renamed("полка")
    cut = overhang(shelf, max_frac=1.0, exclude_zones=[zone])
    assert full.evidence[0].data["area_mm2"] == pytest.approx(100.0)
    assert cut.evidence[0].data["area_mm2"] == pytest.approx(0.0)
    assert "без зон полка: в них нависаний 100.0 мм²" in cut.message


def test_zones_must_be_a_function():
    from datum.build import part

    with pytest.raises(ValueError, match="функция без аргументов"):
        part("x", zones={"a": None})


async def test_the_print_map_leaves_the_excluded_tongue_uncoloured(tmp_path):
    from datum.tools.ops import clear_project_cache
    from datum.tools.registry import OPS

    clear_project_cache()
    plain = _project(tmp_path / "а", WALL % "")
    zoned = _project(tmp_path / "б", WALL % ", exclude_zones: [язычок]")
    a = await OPS["part_view"].fn(project=str(plain), part="гнездо", color_by="print")
    b = await OPS["part_view"].fn(project=str(zoned), part="гнездо", color_by="print")
    assert not a.get("error") and not b.get("error"), (a.get("error"), b.get("error"))
    assert a["print_map"]["thin_mm2"] > 0
    assert b["print_map"]["thin_mm2"] == 0
    assert "тонкое в зонах язычок не показано" in b["report"]


def test_a_check_with_travel_does_not_shift_the_zone(tmp_path):
    """Центр детали в положении хода не годится для поворота зоны: прежде
    проверка с ходом раньше `min_wall` уводила зону с язычка."""
    from datum.geom.measure import bbox

    root = _project(tmp_path, WALL % ", exclude_zones: [язычок]",
                    placement="гнездо: {move: [50, 30, 0], rotate: [0, 0, 90]}\n")
    text = (root / "parts" / "p.py").read_text(encoding="utf-8")
    text = text.replace("def build():", "def build(u=0.0):").replace(
        "    return union(", "    from datum.geom import move\n    return move(union(").replace(
        "_tongue()])", "_tongue()]), dx=u)")
    (root / "parts" / "p.py").write_text(text, encoding="utf-8")
    expected = Project.open(root).zones("гнездо")["язычок"]
    pr = Project.open(root)
    proxy = pr.params.proxy()
    pr._solid("гнездо", proxy, u=8.0)                 # ход — первым
    got = pr.zones("гнездо", proxy)["язычок"]
    assert [*bbox(got)[0], *bbox(got)[1]] == pytest.approx([*bbox(expected)[0], *bbox(expected)[1]])


async def test_the_print_map_reads_a_parametrised_threshold(tmp_path):
    """Порог `${params.…}` молча заменялся на 1.6 — карта показывала не то."""
    from datum.tools.ops import clear_project_cache
    from datum.tools.registry import OPS

    clear_project_cache()
    text = "- id: гнездо.стенки\n  use: min_wall\n  args: {part: '@гнездо', wall: '${params.TONGUE_T}'}\n"
    out = await OPS["part_view"].fn(project=str(_project(tmp_path, text)), part="гнездо",
                                    color_by="print")
    assert not out.get("error"), out.get("error")
    assert "стенка 1 мм" in out["report"], out["report"]
    assert out["print_map"]["thin_mm2"] == 0
