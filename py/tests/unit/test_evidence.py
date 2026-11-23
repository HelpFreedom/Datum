"""Доказательства к проваленным проверкам.

Проверка, сообщающая «пересечение 12.4 мм³», говорит правду и не говорит
ничего: где именно, по какой грани, на сколько залезли. План обещал, что
каждый провал сам приносит картинку; до сих пор поле `evidence` существовало,
а URL в нём был пуст.

Главное требование к картинке — она рисует ТО ЖЕ, что посчитано. Картинка
убеждает сильнее числа, и расходиться с числом ей нельзя.
"""

from __future__ import annotations

import io
import struct

import pytest

from datum.checks.evidence import (best_plane, escape_evidence,
                                   intersection_evidence, pair_evidence,
                                   section_of)
from datum.checks.fit import clearance, no_intersection
from datum.checks.motion import escape
from datum.geom.prims import box
from datum.geom.xform import move


@pytest.fixture
def case():
    return box(0, 60, 0, 40, 0, 20) - box(2, 58, 2, 38, 2, 21)


@pytest.fixture
def intruding_lid():
    """Крышка, уехавшая влево на 3 мм: заведомо сидит в стенке."""
    return move(box(2.3, 57.7, 2.3, 37.7, 13.7, 16.7), dx=-3.0)


def png_size(data: bytes) -> tuple[int, int]:
    """Размер PNG из заголовка — заодно проверка, что это вообще PNG."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "это не PNG"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


# ----------------------------------------------------------- только провал
def test_a_passing_check_draws_nothing(case):
    """Картинка стоит булевой операции, трёх сечений и растеризации. На
    успешной проверке она никому не нужна."""
    far = move(box(2.3, 57.7, 2.3, 37.7, 13.7, 16.7), dz=40)
    result = no_intersection(case, far, a_name="корпус", b_name="крышка")
    assert result.status == "pass"
    assert result.evidence == ()


def test_a_failing_check_brings_a_picture(case, intruding_lid):
    result = no_intersection(case, intruding_lid, a_name="корпус", b_name="крышка")
    assert result.status == "fail"
    pictures = [a for a in result.evidence if a.kind == "png"]
    assert len(pictures) == 1
    assert png_size(pictures[0].data)[0] > 400
    assert "212" in pictures[0].caption, "подпись обязана нести измеренное число"


def test_a_broken_renderer_says_so_instead_of_staying_silent(case, intruding_lid,
                                                             monkeypatch):
    """Именной замок на собственную ошибку.

    Первая версия глотала любое исключение, и опечатка в импорте выглядела как
    «доказательств просто нет». Искать такое потом невесело, поэтому отказ
    рисования кладётся текстом с причиной.
    """
    import datum.checks.fit as fit

    def boom(*args, **kwargs):
        raise RuntimeError("шрифт не нашёлся")

    monkeypatch.setattr(fit, "intersection_evidence", boom)
    result = no_intersection(case, intruding_lid, a_name="корпус", b_name="крышка")
    assert result.status == "fail"
    texts = [a for a in result.evidence if a.kind == "text"]
    assert texts and "шрифт не нашёлся" in texts[0].caption


# ------------------------------------------------------- картинка и число
def test_the_picture_shows_what_was_measured(case, intruding_lid):
    """Сечение обязано проходить ЧЕРЕЗ пересечение, а не мимо.

    Иначе получилась бы картинка, на которой всё в порядке, под сообщением о
    провале — и это хуже, чем никакой картинки.
    """
    from datum.geom.booleans import intersect

    overlap = intersect([case, intruding_lid])
    plane, at = best_plane(case, intruding_lid, through=overlap)
    assert not section_of(overlap, plane, at).is_empty, (
        f"срез {plane}={at} прошёл мимо пересечения")


def test_the_plane_is_chosen_for_context_not_for_size(case, intruding_lid):
    """Правило выбора неочевидное, и оно проверено замером.

    «Перпендикулярно самой короткой оси беды» даёт самое крупное изображение
    наложения — и никакого понимания: крышка в таком срезе целиком совпадает с
    пересечением. Поэтому берётся плоскость, где в сечение попадает больше
    материала: видно, КУДА деталь уехала.
    """
    from datum.geom.booleans import intersect

    overlap = intersect([case, intruding_lid])
    plane, at = best_plane(case, intruding_lid, through=overlap)

    chosen = (section_of(case, plane, at).area
              + section_of(intruding_lid, plane, at).area)
    for other in ("xy", "xz", "yz"):
        if other == plane:
            continue
        # У других плоскостей координата своя, берём ту же точку центра.
        _, other_at = best_plane(case, intruding_lid, through=overlap,
                                 allowed=(other,))
        rival = (section_of(case, other, other_at).area
                 + section_of(intruding_lid, other, other_at).area)
        assert chosen >= rival - 1e-6, f"{other} показала бы больше материала"


def test_escape_draws_the_direction_of_motion(case):
    """Плоскость обязана СОДЕРЖАТЬ направление движения: срез поперёк него
    показал бы две почти одинаковые картинки и ничего не объяснил."""
    loose = box(2.3, 57.7, 2.3, 37.7, 2.3, 5.3)
    result = escape(loose, case, ["+Z"], gap=0.3, hold_vol=1.0,
                    part_name="крышка", holder_name="корпус")[0]
    assert result.status == "fail"
    picture = [a for a in result.evidence if a.kind == "png"][0]
    assert png_size(picture.data)[0] > 400
    assert "НЕ ДЕРЖИТ" in picture.caption


def test_escape_that_passes_draws_nothing():
    """Корпус здесь С ГУБКОЙ: она нависает над крышкой и не даёт ей выйти.

    Без губки крышку ничто не держит, и «не держит» — верный ответ проверки:
    первая проба этого теста была негодной именно поэтому.
    """
    with_lip = (box(0, 60, 0, 40, 0, 20)
                - box(2, 58, 2, 38, 2, 17)          # карман под крышку
                - box(2.8, 57.2, 2.8, 37.2, 17, 21))  # проём уже кармана
    held = box(2.3, 57.7, 2.3, 37.7, 13.7, 16.7)
    for result in escape(held, with_lip, ["+Z"], gap=0.3, hold_vol=1.0,
                         part_name="крышка", holder_name="корпус"):
        assert result.status == "pass", result.message
        assert not [a for a in result.evidence if a.kind == "png"]


def test_clearance_failure_shows_both_parts(case, intruding_lid):
    result = clearance(case, intruding_lid, min_mm=0.3,
                       a_name="корпус", b_name="крышка")
    assert result.status == "fail"
    assert [a for a in result.evidence if a.kind == "png"]


# -------------------------------------------------------------- отрисовка
def test_an_empty_section_is_a_picture_and_not_a_crash():
    """Плоскость увели за деталь. Это обычное дело, и падать тут незачем."""
    from datum.viz.section import Layer, render_section

    data = render_section([Layer(geom=None, label="пусто")], plane="xy", at=999)
    assert png_size(data)[0] > 100


def test_holes_are_really_holes():
    """Дыра в сечении обязана остаться дырой: ImageDraw сам дыр не умеет, и
    заливка «по внешнему контуру» закрасила бы отверстие."""
    from PIL import Image

    from datum.geom.prims import cyl_z
    from datum.viz.section import Layer, render_section

    ring = box(0, 40, 0, 40, 0, 10) - cyl_z(20, -1, 11, x=20, y=20)
    data = render_section([Layer(geom=section_of(ring, "xy", 5), label="кольцо")],
                          plane="xy", at=5)
    image = Image.open(io.BytesIO(data)).convert("RGB")
    # Середина картинки — дыра: цвет обязан остаться фоновым, а не заливкой.
    middle = image.getpixel((image.width // 2, image.height // 2))
    filled = image.getpixel((image.width // 2, int(image.height * 0.12)))
    assert middle != filled, "дыра закрашена"


def test_a_long_caption_is_wrapped_not_cut():
    """Сообщения проверок длинные. Обрезанное на полуслове объяснение хуже
    отсутствующего."""
    from datum.viz.section import Layer, render_section

    long_note = ("крышка +Z: НЕ ДЕРЖИТ — при сдвиге 1.30 мм зацеп 0.00 мм³ < 1.00; "
                 "свободный ход 0.00 мм³. Касание не равно удержанию — ровно на "
                 "этом развалилась v3 в phone-case-gears")
    data = render_section([Layer(geom=section_of(box(0, 10, 0, 10, 0, 10), "xy", 5))],
                          plane="xy", at=5, note=long_note)
    assert png_size(data) == (920, 700)


# ------------------------------------------------------------- публикация
def test_evidence_lands_in_the_cache_with_an_address(latch):
    """Проверка возвращает БАЙТЫ и о кеше не знает — это верно: она считает, а
    не хранит. Но байты нельзя ни показать в окне, ни передать агенту."""
    from datum.doc.project import Project

    lid = latch / "parts" / "lid.py"
    lid.write_text(lid.read_text(encoding="utf-8").replace(
        "return box(P.WALL + P.GAP", "return box(P.WALL + P.GAP - 3.0"),
        encoding="utf-8")

    run = Project.open(latch).run_checks()
    pictures = [a for r in run.results for a in r.evidence if a.kind == "png"]
    assert pictures, "провалы не принесли ни одной картинки"
    for artifact in pictures:
        assert artifact.data is None, "байты не должны уезжать в ответе дважды"
        assert artifact.url == f"/obj/{artifact.digest}.png"
        assert (latch / artifact.path).is_file(), (
            "путь для агента обязан указывать на существующий файл")
