"""Подсказки к ошибкам: чужое имя → наше, опечатка → близкое, неверный
аргумент → подпись. Проверяется и сама таблица (ни одной подсказки на
несуществующее), и путь через настоящий проект."""

from __future__ import annotations

import re

import pytest

import datum.geom as geom
from datum.build.hints import ALIASES, CHECK_ALIASES, hint, names_hint
from datum.checks.library import LIBRARY


def _err(code: str) -> BaseException:
    try:
        exec(code, {})
    except Exception as e:            # noqa: BLE001 — ловим ровно то, что бросит скрипт
        return e
    raise AssertionError("ошибки не было")


def test_every_alias_points_at_functions_that_exist():
    for foreign, (ours, how) in ALIASES.items():
        assert foreign not in geom.__all__, f"{foreign} — уже наше имя, подсказка лишняя"
        for name in ours:
            assert name in geom.__all__ or name == "centre_on_bed", (foreign, name)
            assert name in how, f"{foreign}: в пояснении нет {name}"


def test_every_check_alias_names_a_catalog_check():
    for foreign, target in CHECK_ALIASES.items():
        assert foreign not in LIBRARY
        named = re.findall(r"\b[a-z_]+\b", target)
        assert any(n in LIBRARY for n in named), (foreign, target)
        for n in named:
            if "_" in n:
                assert n in LIBRARY, (foreign, n)


@pytest.mark.parametrize("code, words", [
    ("from datum.geom import cylinder", "cyl_z(d, z0, z1"),
    ("from datum.geom import fillet", "fillet_top=r"),
    ("from datum.geom import centre_onn", "centre_on(s, base"),
    ("import datum.geom as g; g.translate", "move(тело"),
    ("from datum.geom import box; box(0, 1, 0, 1, 0, 1).named('x')", "renamed"),
    ("from datum.geom import box; box(0, 1, 0, 1, 0, 1).translate((1, 0, 0))", "функция, а не метод"),
    ("from datum.geom import box; box(0, 1, 0, 1, 0, 1) & box(0, 1, 0, 1, 0, 1)", "a ^ b"),
    ("from datum.geom import box; box(0, 1, 0, 1, 0, 1, center=True)", "box(x0, x1, y0, y1, z0, z1"),
    ("from datum.geom import box; box(10, 10, 10)", "box(x0, x1"),
    ("from datum.geom import thread; thread(8, 1.25, 0, 10)", "clearance"),
    ("from datum.geom import slab; slab(None, 0, 1, fillet=1)", "fillet_top=…"),
    ("cyl_z(3, 0, 1)", "from datum.geom import cyl_z"),
    ("from datum.geometry import box", "модуль — datum.geom"),
    ("centre_on_bed(1)", "from datum.build import centre_on_bed"),
])
def test_a_typical_mistake_gets_its_replacement(code, words):
    assert words in hint(_err(code))


def test_nothing_is_guessed_where_there_is_nothing_to_say():
    assert hint(_err("1 / 0")) == ""
    assert hint(_err("from os import zzz_nothing")) == ""
    assert hint(_err("def mine(a): pass\nmine(b=1)")) == ""     # своя функция — не наша забота
    assert hint(_err("1 & 'a'")) == ""


def test_names_given_as_one_string_or_misspelt():
    parts = ["коробка", "крышка", "ось"]
    assert "по одному" in names_hint(["коробка,крышка"], parts)
    assert names_hint(["крышко"], parts) == "'крышко' → 'крышка'"
    assert names_hint(["совсем-другое"], parts) == ""
    assert "joints:" in names_hint(["hinge"], LIBRARY, aliases=CHECK_ALIASES)


# ------------------------------------------------------------------ в проекте
def test_hints_reach_the_project_messages(cube):
    from datum.doc.project import Project, ProjectError

    bad = cube / "parts" / "bad.py"
    bad.write_text("from datum.geom import cylinder\n", encoding="utf-8")
    with pytest.raises(ProjectError, match="Подсказка: цилиндр — cyl_z"):
        Project.open(cube)

    bad.write_text('''from datum.build import part
from datum.geom import box


@part("плохая", print_up="+Z")
def build():
    return box(0, 10, 0, 10, 0, 10).translate((5, 0, 0))
''', encoding="utf-8")
    res = Project.open(cube).build(["плохая"])["плохая"]
    assert not res.ok and "Подсказка: это функция, а не метод: перенос — move" in res.line()

    bad.unlink()
    with pytest.raises(ProjectError, match="Подсказка: .кубек. → .кубик."):
        Project.open(cube).build(["кубек"])


def test_a_wrong_check_argument_shows_the_signature(cube):
    from datum.doc.project import Project, ProjectError

    checks = cube / "checks" / "checks.yaml"
    base = checks.read_text(encoding="utf-8")
    checks.write_text(base + '''
- id: стенка.опечатка
  use: min_wall
  args: {part: "@кубик", wal: 1.0}
''', encoding="utf-8")
    run = Project.open(cube).run_checks()
    broken = [r for r in run.results if r.id == "стенка.опечатка"][0]
    assert broken.status == "error" and "Подсказка:" in broken.message
    assert "wall=…" in broken.message and "min_wall(part" in broken.message

    checks.write_text(base + '''
- id: петля
  use: hinge
  args: {part: "@кубик"}
''', encoding="utf-8")
    with pytest.raises(ProjectError, match="joints:"):
        Project.open(cube).run_checks()
