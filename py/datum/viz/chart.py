"""Графики к проверкам: когда ответ — кривая, а не число.

Правило проекта: число говорит, ЧТО не так, картинка — ГДЕ. У проверок
контакта и МКЭ самое полезное вообще не число, а КРИВАЯ: профиль отжатия вдоль
пути вставки и лестница сходимости по сеткам. Пока они приходили только в
JSON, их не видел никто — панель проверок рисует исключительно PNG, а
остальное молча пропускает.

Палитра, шрифт и разметка берутся из `viz.section`: у картинок к проверкам
должен быть один вид, а два набора цветов в одном окне выглядели бы как
недосмотр.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from io import BytesIO

from PIL import Image, ImageDraw

from datum.viz.section import (BACKGROUND, BAD, DIM, GRID_MAJOR, GRID_MINOR,
                               TEXT, _font, _nice_step, _num, _ticks)

__all__ = ["Series", "Mark", "render_chart"]

#: Цвета кривых по порядку. Первый — основной.
LINES = ((122, 162, 247), (158, 206, 106), (224, 175, 104), (187, 154, 247))


@dataclass
class Series:
    """Одна кривая."""

    xs: Sequence[float]
    ys: Sequence[float]
    label: str = ""
    colour: tuple[int, int, int] = LINES[0]
    width: int = 2
    dots: bool = False
    dashed: bool = False


@dataclass
class Mark:
    """Отметка: вертикаль, горизонталь или точка с подписью."""

    label: str = ""
    x: float | None = None
    y: float | None = None
    colour: tuple[int, int, int] = BAD
    dashed: bool = True


@dataclass
class Band:
    """Полоса неопределённости по Y — вилка ответа."""

    lo: float
    hi: float
    label: str = ""
    colour: tuple[int, int, int] = (122, 162, 247)


def _dashes(draw, a, b, colour, width, on=6, off=5) -> None:
    (x0, y0), (x1, y1) = a, b
    length = math.hypot(x1 - x0, y1 - y0)
    if length <= 0:
        return
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    at = 0.0
    while at < length:
        end = min(at + on, length)
        draw.line([(x0 + ux * at, y0 + uy * at), (x0 + ux * end, y0 + uy * end)],
                  fill=colour, width=width)
        at = end + off


def render_chart(series: Iterable[Series], *, title: str = "", note: str = "",
                 x_label: str = "", y_label: str = "",
                 marks: Iterable[Mark] = (), bands: Iterable[Band] = (),
                 size: tuple[int, int] = (920, 520),
                 x_log: bool = False) -> bytes:
    """Нарисовать график и вернуть PNG байтами.

    `x_log` нужен лестнице сеток: число степеней свободы растёт в разы, и на
    равномерной оси три точки сбиваются в угол.
    """
    series = [s for s in series if len(s.xs) and len(s.ys)]
    if not series:
        raise ValueError("рисовать нечего: ни одной кривой с точками")

    width, height = size
    image = Image.new("RGB", size, BACKGROUND)
    draw = ImageDraw.Draw(image)
    small, normal, big = _font(12), _font(14), _font(16)

    plot = (80, 56, width - 24, height - (72 if note else 52))
    marks = list(marks)
    bands = list(bands)

    xs = [x for s in series for x in s.xs]
    ys = [y for s in series for y in s.ys]
    ys += [m.y for m in marks if m.y is not None]
    for band in bands:
        ys += [band.lo, band.hi]
    x_lo, x_hi = min(xs), max(xs)
    y_lo, y_hi = min(ys), max(ys)
    # Немного воздуха сверху и снизу, иначе кривая липнет к рамке.
    pad = (y_hi - y_lo) * 0.08 or max(abs(y_hi), 1.0) * 0.08
    y_lo, y_hi = y_lo - pad, y_hi + pad
    if x_log:
        x_lo = max(x_lo, 1e-9)
        lx_lo, lx_hi = math.log10(x_lo), math.log10(max(x_hi, x_lo * 1.001))
        margin = (lx_hi - lx_lo) * 0.06 or 0.05
        lx_lo, lx_hi = lx_lo - margin, lx_hi + margin
    if x_hi - x_lo <= 0:
        x_hi = x_lo + 1.0

    def to_px(x: float, y: float) -> tuple[float, float]:
        if x_log:
            t = (math.log10(max(x, 1e-9)) - lx_lo) / max(lx_hi - lx_lo, 1e-9)
        else:
            t = (x - x_lo) / (x_hi - x_lo)
        u = (y - y_lo) / max(y_hi - y_lo, 1e-30)
        return (plot[0] + t * (plot[2] - plot[0]),
                plot[3] - u * (plot[3] - plot[1]))

    # --- сетка
    if x_log:
        # На логарифмической оси круглые деления бесполезны: подписываются САМИ
        # ТОЧКИ. У лестницы сеток их три-четыре, и число степеней свободы —
        # именно то, что читатель хочет знать. Без этого ось была немой.
        seen = sorted({float(x) for s_ in series for x in s_.xs})
        for value in seen[:10]:
            px = to_px(value, y_lo)[0]
            draw.line([(px, plot[1]), (px, plot[3])], fill=GRID_MINOR)
            text = f"{value:,.0f}".replace(",", " ")
            draw.text((px - 4 * len(text), plot[3] + 5), text, font=small, fill=DIM)
    else:
        step = _nice_step(x_hi - x_lo)
        for value in _ticks(x_lo, x_hi, step):
            px = to_px(value, y_lo)[0]
            draw.line([(px, plot[1]), (px, plot[3])], fill=GRID_MINOR)
            draw.text((px - 8, plot[3] + 5), _num(value), font=small, fill=DIM)
    y_step = _nice_step(y_hi - y_lo)
    for value in _ticks(y_lo, y_hi, y_step):
        py = to_px(x_lo, value)[1]
        draw.line([(plot[0], py), (plot[2], py)],
                  fill=GRID_MAJOR if abs(value) < 1e-12 else GRID_MINOR)
        draw.text((plot[0] - 72, py - 7), _num(value), font=small, fill=DIM)

    # --- вилки
    for band in bands:
        top, bottom = to_px(x_lo, band.hi)[1], to_px(x_lo, band.lo)[1]
        overlay = Image.new("RGBA", size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rectangle(
            [plot[0], min(top, bottom), plot[2], max(top, bottom)],
            fill=(*band.colour, 38))
        image.alpha_composite(overlay) if image.mode == "RGBA" else image.paste(
            Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB"))
        draw = ImageDraw.Draw(image)
        if band.label:
            draw.text((plot[0] + 6, min(top, bottom) + 3), band.label,
                      font=small, fill=band.colour)

    # --- кривые
    for line in series:
        points = [to_px(x, y) for x, y in zip(line.xs, line.ys)]
        if len(points) > 1:
            if line.dashed:
                for a, b in zip(points, points[1:]):
                    _dashes(draw, a, b, line.colour, line.width)
            else:
                draw.line(points, fill=line.colour, width=line.width, joint="curve")
        if line.dots or len(points) == 1:
            for px, py in points:
                draw.ellipse([px - 3, py - 3, px + 3, py + 3], fill=line.colour)

    # --- отметки
    for mark in marks:
        if mark.x is not None:
            px = to_px(mark.x, y_lo)[0]
            _dashes(draw, (px, plot[1]), (px, plot[3]), mark.colour, 1) if mark.dashed \
                else draw.line([(px, plot[1]), (px, plot[3])], fill=mark.colour)
            if mark.label:
                # Подпись внизу: наверху сидит легенда, и они налезали.
                draw.text((px + 4, plot[3] - 16), mark.label, font=small,
                          fill=mark.colour)
        elif mark.y is not None:
            py = to_px(x_lo, mark.y)[1]
            _dashes(draw, (plot[0], py), (plot[2], py), mark.colour, 1)
            if mark.label:
                # Справа: слева подписывается вилка, и они налезали.
                draw.text((plot[2] - 8 * len(mark.label) - 8, py - 16), mark.label,
                          font=small, fill=mark.colour)

    # --- рамка и подписи
    draw.rectangle(list(plot), outline=GRID_MAJOR)
    if title:
        draw.text((24, 18), title, font=big, fill=TEXT)
    if x_label:
        draw.text((plot[2] - 8 * len(x_label), plot[3] + 22), x_label,
                  font=small, fill=DIM)
    if y_label:
        draw.text((plot[0] - 72, plot[1] - 20), y_label, font=small, fill=DIM)
    if note:
        draw.text((24, height - 34), note[:170], font=normal, fill=DIM)

    # --- легенда: НАД полем графика, а не в нём. Внутри она садилась ровно
    # туда, где у пологой кривой проходит сама кривая.
    legend = [s for s in series if s.label]
    x = plot[2]
    for line in reversed(legend):
        text_width = 8 * len(line.label)
        x -= text_width + 40
        draw.line([(x, 26), (x + 26, 26)], fill=line.colour, width=3)
        draw.text((x + 32, 18), line.label, font=small, fill=TEXT)

    out = BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()
