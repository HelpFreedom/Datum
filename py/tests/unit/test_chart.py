"""Графики к проверкам.

Правило проекта: число говорит, ЧТО не так, картинка — ГДЕ. У проверок
контакта и МКЭ самое полезное — кривая, а не число: профиль отжатия вдоль пути
и лестница сходимости. Пока они приходили только в JSON, их не видел никто:
панель проверок рисует исключительно PNG.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from datum.viz.chart import Band, Mark, Series, render_chart


def _image(png: bytes) -> Image.Image:
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "это не PNG"
    return Image.open(io.BytesIO(png))


def test_a_chart_is_a_real_image_of_the_asked_size():
    png = render_chart([Series([0, 1, 2], [0, 1, 0.5], label="проба")],
                       title="заголовок", x_label="мм", y_label="мм",
                       size=(640, 400))
    image = _image(png)
    assert image.size == (640, 400)
    # Картинка не должна выйти однотонной: значит что-то нарисовалось.
    assert len(image.convert("RGB").getcolors(maxcolors=1 << 16)) > 5


def test_marks_and_bands_do_not_break_anything():
    png = render_chart([Series([0, 1, 2], [0, 1, 2])],
                       marks=[Mark(label="слева", x=0.5), Mark(label="снизу", y=1.0)],
                       bands=[Band(0.8, 1.2, "вилка")])
    assert _image(png).size == (920, 520)


def test_a_log_axis_survives_a_single_point():
    """У лестницы сеток бывает две ступени, а то и одна: деление на нулевой
    размах оси не должно ронять рисование."""
    png = render_chart([Series([1000], [2.0], dots=True)], x_log=True)
    assert _image(png)


def test_an_empty_chart_is_refused():
    """Пустая картинка выглядит как «доказательств нет», а на деле их не
    построили. Лучше отказ с причиной."""
    with pytest.raises(ValueError, match="рисовать нечего"):
        render_chart([])
    with pytest.raises(ValueError, match="рисовать нечего"):
        render_chart([Series([], [])])


def test_a_flat_curve_does_not_divide_by_zero():
    png = render_chart([Series([0, 1, 2], [5.0, 5.0, 5.0])])
    assert _image(png)


# --------------------------------------------------- картинки у проверок
def test_the_contact_check_brings_a_profile_picture():
    """Здесь картинка рисуется ВСЕГДА, а не только при провале, и это не
    отступ от правила. Правило запрещает платить БУЛЕВЫМИ за доказательство к
    успешной проверке; тут развёртка уже пройдена, остаётся растеризация —
    около 30 мс. А по кривой сразу видно то, что из текста читается плохо:
    есть скос или отжатие возникает ступенькой.
    """
    from datum.checks.contact import insertion_force
    from datum.geom.booleans import diff
    from datum.geom.prims import box

    peg = box(-5.5, 5.5, -5.5, 5.5, 0, 10).renamed("палец")
    plate = diff(box(-30, 30, -30, 30, 10, 11),
                 [box(-5, 5, -5, 5, 9, 12)]).renamed("плита")
    result = insertion_force(peg, plate, direction="-Z", distance=12.0, samples=21,
                             spring_length=18.0, spring_width=60.0,
                             spring_thickness=2.0, modulus=2000.0, poisson=0.35,
                             friction=0.3, divisions=(8, 4, 2), part_name="палец")
    pictures = [a for a in result.evidence if a.kind == "png"]
    assert pictures, [a.kind for a in result.evidence]
    assert _image(pictures[0].data).size[0] > 400
    assert "отжатия" in pictures[0].caption


def test_a_broken_renderer_says_why_instead_of_going_quiet():
    """Молчание тут хуже отсутствия картинки: опечатка в импорте выглядела бы
    как «доказательств просто нет». Этот урок уже оплачен однажды."""
    import datum.checks.contact as module

    original = module._profile_picture
    try:
        def explode(*args, **kwargs):
            raise RuntimeError("нарочно сломано")

        # Ломаем то, что внутри, а не саму обёртку: обёртка обязана поймать.
        import datum.viz.chart as chart

        broken = chart.render_chart
        chart.render_chart = explode
        try:
            from datum.checks.contact import InterferenceProfile
            artifacts = module._profile_picture(
                InterferenceProfile(path=(0.0, 1.0), depth=(0.0, 0.5)),
                0.5, "деталь", "проба")
        finally:
            chart.render_chart = broken
    finally:
        module._profile_picture = original

    assert len(artifacts) == 1 and artifacts[0].kind == "text"
    assert "нарочно сломано" in artifacts[0].caption
