"""Сечение в PNG — доказательство к проваленной проверке.

Зачем это вообще. Проверка, сообщающая «пересечение 12.4 мм³», говорит правду и
не говорит ничего: где пересечение, по какой грани, на сколько миллиметров
залезли. Разбирать такое приходится руками — построить сечение, посмотреть,
померить. План обещал, что каждый провал сам приносит картинку, на которой
виновное место видно глазом; до сих пор поле `evidence` у результата
существовало, а URL в нём был пуст.

Рисуется РОВНО ТО, что посчитано: те же полигоны сечения, по которым считалась
площадь. Нарисовать «примерно похожее» было бы хуже, чем не рисовать вовсе —
картинка убеждает сильнее числа, и расходиться с числом ей нельзя.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from PIL import Image, ImageDraw, ImageFont

__all__ = ["Layer", "render_section", "PLANE_AXES"]

#: Какие оси лежат в плоскости сечения. Совпадает с тем, что отдаёт
#: `datum.geom.measure.section_*`, и выверено там на несимметричном теле.
PLANE_AXES = {"xy": ("X", "Y"), "xz": ("X", "Z"), "yz": ("Y", "Z")}

BACKGROUND = (22, 24, 29)
GRID_MINOR = (37, 42, 51)
GRID_MAJOR = (57, 64, 78)
TEXT = (200, 204, 212)
DIM = (125, 132, 146)
BAD = (229, 140, 140)

_FONT_PATHS = (
    # Встроенный: он есть всегда и одинаков везде, поэтому картинки совпадают
    # от машины к машине. Прежде список начинался с путей Debian — на macOS и
    # в любом другом месте брался запасной шрифт Pillow, мелкий и без
    # кириллицы, и подписи на сечениях рисовались не тем, чем задумано.
    str(Path(__file__).resolve().parent.parent / "assets" / "fonts" / "DejaVuSans.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
)


def _font(size: int):
    for path in _FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    # Встроенный шрифт мелкий и без кириллицы, но лучше, чем отказ рисовать.
    return ImageFont.load_default()          # pragma: no cover


@dataclass
class Layer:
    """Один слой картинки: геометрия, цвет и подпись в легенде."""

    geom: Any                                 # Polygon или MultiPolygon shapely
    label: str = ""
    fill: tuple[int, int, int, int] = (154, 164, 178, 110)
    line: tuple[int, int, int, int] = (154, 164, 178, 255)
    width: int = 2
    extras: list = field(default_factory=list)   # точки (x, y, подпись)


def _polygons(geom) -> list:
    if geom is None or getattr(geom, "is_empty", True):
        return []
    return list(geom.geoms) if hasattr(geom, "geoms") else [geom]


def _bounds(layers: Sequence[Layer]) -> tuple[float, float, float, float] | None:
    boxes = [layer.geom.bounds for layer in layers
             if layer.geom is not None and not layer.geom.is_empty]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _nice_step(span: float) -> float:
    """Шаг сетки: круглое число, дающее от 5 до 15 линий."""
    if span <= 0:
        return 1.0
    for step in (0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500):
        if span / step <= 15:
            return float(step)
    return span / 10.0                        # pragma: no cover


def render_section(layers: Iterable[Layer], *, plane: str = "xy", at: float = 0.0,
                   title: str = "", note: str = "", size: tuple[int, int] = (920, 700),
                   pad: int = 56, where: str | None = None) -> bytes:
    """Нарисовать сечение. Возвращает PNG байтами.

    `where` заменяет подпись положения («Y = 1.25 мм») — для картинок, которые
    не сечение в одной плоскости, а, например, тень слоя.
    """
    layers = [layer for layer in layers if layer.geom is not None]
    width, height = size
    image = Image.new("RGB", size, BACKGROUND)
    draw = ImageDraw.Draw(image)
    small, normal, big = _font(12), _font(14), _font(16)

    box = _bounds(layers)
    if box is None:
        draw.text((pad, height // 2), "сечение пустое: плоскость не пересекает тело",
                  font=normal, fill=DIM)
        return _png(image)

    x0, y0, x1, y1 = box
    span_x, span_y = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    margin = 0.06 * max(span_x, span_y)
    x0, y0, x1, y1 = x0 - margin, y0 - margin, x1 + margin, y1 + margin
    span_x, span_y = x1 - x0, y1 - y0

    top = pad if not title else pad + 12
    # Снизу оставлено место под перенесённую на две строки подпись.
    bottom = height - pad - (17 if note else 0)
    plot = (pad, top, width - pad // 2, bottom)
    scale = min((plot[2] - plot[0]) / span_x, (plot[3] - plot[1]) / span_y)
    # Центрируем поле рисунка, чтобы деталь не липла к краю.
    off_x = plot[0] + ((plot[2] - plot[0]) - span_x * scale) / 2
    off_y = plot[1] + ((plot[3] - plot[1]) - span_y * scale) / 2

    def to_px(point) -> tuple[float, float]:
        # Y на экране растёт вниз, в геометрии — вверх.
        return (off_x + (point[0] - x0) * scale,
                off_y + (y1 - point[1]) * scale)

    _grid(draw, (x0, y0, x1, y1), to_px, plot, small)

    for layer in layers:
        _draw_layer(image, draw, layer, to_px)

    _frame_and_labels(draw, plane, at, title, note, layers,
                      plot, size, (small, normal, big), where)
    return _png(image)


def _grid(draw, bounds, to_px, plot, font) -> None:
    x0, y0, x1, y1 = bounds
    step = _nice_step(max(x1 - x0, y1 - y0))
    minor = step / 5.0

    def line_at(value, vertical, colour):
        if vertical:
            px = to_px((value, y0))[0]
            draw.line([(px, plot[1]), (px, plot[3])], fill=colour)
        else:
            py = to_px((x0, value))[1]
            draw.line([(plot[0], py), (plot[2], py)], fill=colour)

    # Мелкая сетка только если она не превращается в заливку.
    if (x1 - x0) / minor < 120:
        for value in _ticks(x0, x1, minor):
            line_at(value, True, GRID_MINOR)
        for value in _ticks(y0, y1, minor):
            line_at(value, False, GRID_MINOR)

    for value in _ticks(x0, x1, step):
        line_at(value, True, GRID_MAJOR)
        px = to_px((value, y0))[0]
        draw.text((px + 2, plot[3] + 4), _num(value), font=font, fill=DIM)
    for value in _ticks(y0, y1, step):
        line_at(value, False, GRID_MAJOR)
        py = to_px((x0, value))[1]
        draw.text((plot[0] - 40, py - 7), _num(value), font=font, fill=DIM)


def _ticks(lo: float, hi: float, step: float) -> list[float]:
    import math

    first = math.ceil(lo / step) * step
    out, value = [], first
    while value <= hi + 1e-9 and len(out) < 400:
        out.append(round(value, 6))
        value += step
    return out


def _num(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _draw_layer(image, draw, layer: Layer, to_px) -> None:
    """Заливка с дырами через маску: ImageDraw сам дыр не умеет."""
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    mask = Image.new("L", image.size, 0)
    mask_draw = ImageDraw.Draw(mask)

    for polygon in _polygons(layer.geom):
        shell = [to_px(p) for p in polygon.exterior.coords]
        if len(shell) >= 3:
            mask_draw.polygon(shell, fill=255)
        for hole in polygon.interiors:
            points = [to_px(p) for p in hole.coords]
            if len(points) >= 3:
                mask_draw.polygon(points, fill=0)

    overlay.paste(Image.new("RGBA", image.size, layer.fill), mask=mask)
    image.paste(Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB"),
                (0, 0))

    outline = ImageDraw.Draw(image)
    for polygon in _polygons(layer.geom):
        for ring in [polygon.exterior, *polygon.interiors]:
            points = [to_px(p) for p in ring.coords]
            if len(points) >= 2:
                outline.line(points + [points[0]], fill=layer.line[:3], width=layer.width)

    for x, y, text in layer.extras:
        px, py = to_px((x, y))
        radius = 4
        outline.ellipse([px - radius, py - radius, px + radius, py + radius],
                        outline=BAD, width=2)
        if text:
            outline.text((px + 7, py - 8), text, font=_font(12), fill=BAD)


def _frame_and_labels(draw, plane, at, title, note, layers, plot, size, fonts,
                      where: str | None = None) -> None:
    small, normal, big = fonts
    width, height = size
    draw.rectangle([plot[0], plot[1], plot[2], plot[3]], outline=GRID_MAJOR)

    axes = PLANE_AXES.get(plane, ("U", "V"))
    # Подписи осей — ПОД метками и НАД полем, иначе они наползают на числа.
    draw.text((plot[2], plot[3] + 22), f"{axes[0]}, мм", font=small,
              fill=DIM, anchor="ra")
    draw.text((plot[0] - 40, plot[1] - 18), f"{axes[1]}, мм", font=small, fill=DIM)

    head = title or f"сечение {plane.upper()}"
    draw.text((plot[0] - 40, 12), head, font=big, fill=TEXT)
    # Положение сечения — по НОРМАЛИ к плоскости: срез XZ стоит на каком-то Y.
    # Раньше здесь стояло «XZ = 1.25 мм», и читалось это как координата по X.
    across = {"xy": "Z", "xz": "Y", "yz": "X"}.get(plane.lower(), plane.upper())
    draw.text((plot[2], 14), where if where is not None else f"{across} = {_num(at)} мм",
              font=normal,
              fill=DIM, anchor="ra")

    if note:
        # Подпись переносится по словам: сообщения проверок длинные, и
        # обрезанное на полуслове объяснение хуже отсутствующего.
        lines = _wrap(note, normal, width - 80, draw)[:2]
        for i, line in enumerate(lines):
            draw.text((plot[0] - 40, height - 24 - (len(lines) - 1 - i) * 17),
                      line, font=normal, fill=TEXT)

    # Легенда: без неё два похожих силуэта не различить.
    y = plot[1] + 8
    for layer in layers:
        if not layer.label:
            continue
        draw.rectangle([plot[2] - 168, y, plot[2] - 152, y + 12],
                       fill=layer.fill[:3], outline=layer.line[:3])
        draw.text((plot[2] - 146, y - 1), layer.label, font=small, fill=TEXT)
        y += 18


def _wrap(text: str, font, limit: int, draw) -> list[str]:
    """Разбить строку по словам под заданную ширину в пикселях."""
    words, lines, current = text.split(), [], ""
    for word in words:
        probe = f"{current} {word}".strip()
        if draw.textlength(probe, font=font) <= limit or not current:
            current = probe
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
