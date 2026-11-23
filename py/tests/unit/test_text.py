"""Надписи: контуры букв, шрифты проекта, ключ кеша, операции шрифтов."""

from __future__ import annotations

import asyncio
import shutil

import pytest

from datum.doc.project import Project
from datum.geom.text import (BUNDLED_FONTS, FontError, font_names, font_path,
                             project_fonts_stamp, text_poly, text_z)
from datum.geom.topology import analyze


def test_the_size_is_the_capital_height():
    h = text_poly("H", 10, valign="baseline")
    assert h.bounds[1] == pytest.approx(0, abs=1e-6) and h.bounds[3] == pytest.approx(10, abs=1e-6)


def test_counters_are_holes_and_cyrillic_is_there():
    o = text_poly("О", 10)                      # кириллическая О
    assert o.geom_type == "Polygon" and len(o.interiors) == 1
    word = text_poly("Ёжик 42", 6)
    assert word.is_valid and len(word.geoms) >= 6
    # По центру ширин знаков (как в типографике), а не чернил: боковые поля
    # первой и последней буквы разные, и границы расходятся на сотые.
    assert word.bounds[0] == pytest.approx(-word.bounds[2], abs=0.2), "по центру"


def test_bold_is_bolder_and_alignment_moves_the_text():
    assert text_poly("Bold", 8, font="DejaVu Sans Bold").area > text_poly("Bold", 8).area * 1.4
    left = text_poly("Ab", 5, halign="left", valign="bottom")
    assert left.bounds[0] >= -0.5 and left.bounds[1] == pytest.approx(0, abs=1e-6)


def test_a_missing_glyph_or_font_is_refused_not_dropped():
    with pytest.raises(FontError, match="нет знаков"):
        text_poly("a中", 5)                     # иероглифов в DejaVu Sans нет
    with pytest.raises(FontError, match="не найден.*font_add"):
        text_poly("a", 5, font="Нет Такого")


def test_text_as_a_solid_is_closed():
    s = text_z("АБВ", 6, 0, 0.8, 50, 50, angle=30)
    rep = analyze(s.manifold)
    assert rep.status == "NoError" and rep.bodies == 3


def test_project_fonts_come_first_and_change_the_cache_key(cube):
    """Шрифт проекта находится по имени, и его замена пересобирает надпись."""
    fonts = cube / "fonts"
    fonts.mkdir()
    assert project_fonts_stamp(cube) == ""
    shutil.copy(BUNDLED_FONTS / "DejaVuSans-Bold.ttf", fonts / "Mine.ttf")
    assert font_path("DejaVu Sans Bold", cube) == fonts / "Mine.ttf"
    assert font_names(cube)["проект"] == ["DejaVu Sans Bold"]
    stamp = project_fonts_stamp(cube)
    assert stamp

    part = cube / "parts" / "label.py"
    part.write_text('''from datum.build import centre_on_bed, part
from datum.geom import box, text_z, union


@part("табличка", print_up="+Z")
def build():
    plate = box(-30, 30, -8, 8, 0, 2)
    return centre_on_bed(union([plate, text_z("Datum", 8, 1.9, 2.8, font="DejaVu Sans Bold")]))
''', encoding="utf-8")
    first = Project.open(cube).build(["табличка"])["табличка"]
    assert first.ok and not first.cached
    again = Project.open(cube).build(["табличка"])["табличка"]
    assert again.cached
    shutil.copy(BUNDLED_FONTS / "DejaVuSans.ttf", fonts / "Mine.ttf")   # тот же файл — другой шрифт
    assert project_fonts_stamp(cube) != stamp
    rebuilt = Project.open(cube).build(["табличка"])["табличка"]
    assert rebuilt.ok and not rebuilt.cached


def test_font_operations_list_and_copy_into_the_project(cube, tmp_path, monkeypatch):
    import datum.geom.text as text_module
    from datum.tools.ops import clear_project_cache, font_add, fonts_list

    system = tmp_path / "system"
    system.mkdir()
    shutil.copy(BUNDLED_FONTS / "DejaVuSans-Bold.ttf", system / "Fancy.ttf")
    monkeypatch.setattr(text_module, "system_fonts",
                        lambda: {"DejaVu Sans Bold": system / "Fancy.ttf"})
    clear_project_cache()
    listed = asyncio.run(fonts_list(project=str(cube), query="bold"))
    assert listed["system"] == ["DejaVu Sans Bold"] and listed["project_fonts"] == []
    added = asyncio.run(font_add(project=str(cube), name="dejavu sans bold"))
    assert added["font"] == "DejaVu Sans Bold" and (cube / "fonts" / "Fancy.ttf").is_file()
    again = asyncio.run(font_add(project=str(cube), name="DejaVu Sans Bold"))
    assert "уже в проекте" in again["report"]
    missing = asyncio.run(font_add(project=str(cube), name="DejaVu Bld"))
    assert "похожие" in missing["error"]


# ------------------------------------------------------------------ кернинг
def _gaps(poly) -> list[float]:
    """Щели между соседними знаками слева направо."""
    parts = sorted(poly.geoms if hasattr(poly, "geoms") else [poly], key=lambda g: g.bounds[0])
    return [a.distance(b) for a, b in zip(parts, parts[1:])]


def test_kerning_pulls_the_pairs_the_font_kerns():
    """«AV», «To», «P.» — пары DejaVu: с кернингом надпись уже, буквы те же."""
    for word in ("AV", "To", "P."):
        plain, kerned = text_poly(word, 8, kerning=False), text_poly(word, 8)
        assert kerned.bounds[2] - kerned.bounds[0] < plain.bounds[2] - plain.bounds[0] - 0.3
        assert kerned.area == pytest.approx(plain.area, rel=1e-9)     # буквы не налезли


def test_without_kerning_the_layout_is_the_old_one():
    """`kerning=False` — набор по одним ширинам, как было до HarfBuzz: сумма
    ширин знаков (hmtx) и ничего больше. Свёрено и с прежней версией модуля
    на кириллице, латинице, строках, разрядке и выравнивании — до 2e-12 мм²."""
    from fontTools.ttLib import TTFont

    tt = TTFont(str(font_path("DejaVu Sans")))
    cmap, hmtx = tt.getBestCmap(), tt["hmtx"]
    word = "AVATAR"
    widths = sum(hmtx[cmap[ord(ch)]][0] for ch in word)
    plain = text_poly(word, 10, halign="left", valign="baseline", kerning=False)
    kerned = text_poly(word, 10, halign="left", valign="baseline")
    scale = 10 / tt["glyf"][cmap[ord("H")]].yMax      # высота «H», как меряет модуль
    # Последняя R начинается там, где кончаются ширины всех знаков до неё, и
    # её чернила — те же, что у одиночной R.
    r_start = (widths - hmtx[cmap[ord("R")]][0]) * scale
    r_ink = text_poly("R", 10, halign="left", valign="baseline", kerning=False)
    assert plain.bounds[2] == pytest.approx(r_start + r_ink.bounds[2], abs=1e-6)
    assert kerned.bounds[2] < plain.bounds[2] - 1.0


def test_kerning_never_squeezes_letters_below_the_floor():
    """Кернинг жирного «To» и «LT» на 4 мм давал щель 0.44 мм. Предел — не
    теснее min(щель без кернинга, KERN_MIN_GAP_MM): пара, тесная и без него
    («Ty» жирным, 0.24 мм), остаётся как была, а не становится хуже."""
    from datum.geom.text import KERN_MIN_GAP_MM

    for font in ("DejaVu Sans", "DejaVu Sans Bold"):
        for word in ("To", "LT", "Ty", "Yo", "AVATAR", "WAVY Tow", "P."):
            for size in (3.0, 4.0, 6.0):
                plain = _gaps(text_poly(word, size, font=font, kerning=False))
                kerned = _gaps(text_poly(word, size, font=font))
                for a, b in zip(plain, kerned):
                    assert b >= min(a, KERN_MIN_GAP_MM) - 1e-6, (font, word, size, a, b)
    to = _gaps(text_poly("To", 4, font="DejaVu Sans Bold"))
    assert to == [pytest.approx(KERN_MIN_GAP_MM, abs=1e-6)]       # свело ровно до предела
    big = _gaps(text_poly("To", 12, font="DejaVu Sans Bold"))
    assert big[0] > KERN_MIN_GAP_MM + 0.5          # крупно — кернинг шрифта целиком


def test_cyrillic_is_kerned_by_fonts_that_kern_it(cube):
    """У DejaVu пар кириллицы нет — набор по ширинам; у Liberation Sans есть."""
    plain = text_poly("Г. Ту", 8, kerning=False)
    assert text_poly("Г. Ту", 8).bounds == pytest.approx(plain.bounds, abs=1e-9)
    liberation = next((p for p in ("/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
                                   "/usr/share/fonts/TTF/LiberationSans-Regular.ttf")
                       if __import__("os").path.isfile(p)), None)
    if liberation is None:
        pytest.skip("Liberation Sans не установлен")
    (cube / "fonts").mkdir(exist_ok=True)
    shutil.copy(liberation, cube / "fonts")
    from datum.build.context import use_project  # шрифты проекта — по контексту сборки

    with use_project(cube):
        a = text_poly("Г.", 8, font="Liberation Sans", kerning=False)
        b = text_poly("Г.", 8, font="Liberation Sans")
    assert b.bounds[2] - b.bounds[0] < a.bounds[2] - a.bounds[0] - 0.5


def test_spaces_and_spacing_still_work_with_kerning():
    one = text_poly("A V", 8)
    two = text_poly("A  V", 8)
    assert two.bounds[2] - two.bounds[0] > one.bounds[2] - one.bounds[0] + 1.0
    wide = text_poly("AV", 8, spacing=1.0)
    assert wide.bounds[2] - wide.bounds[0] == pytest.approx(
        text_poly("AV", 8).bounds[2] - text_poly("AV", 8).bounds[0] + 1.0, abs=1e-6)
