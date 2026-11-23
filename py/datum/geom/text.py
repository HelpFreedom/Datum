"""Надписи как геометрия: контуры букв шрифта → плоский профиль → тело.

Тиснение и гравировка для печати: `text_poly` даёт плоский профиль shapely
(как `rrect_poly`), `text_z` — выдавленное тело, которое прибавляют к детали
(выпуклая надпись) или вычитают из неё (вдавленная).

ШРИФТ — по имени. Ищется сначала в `fonts/` проекта, потом среди встроенных
(`datum/assets/fonts`: DejaVu Sans, обычный и жирный, — кириллица есть).
Системный шрифт в проект копирует операция `font_add`: так проект
самодостаточен (архив `.datum` несёт шрифт с собой), а сборка
воспроизводима — шрифты проекта входят в ключ кеша, как модели `imported/`.

РАЗМЕР — высота ЗАГЛАВНОЙ буквы в мм («H»), а не кегль: так её и меряют
штангенциркулем на напечатанном. Для сопла 0.4 обычный DejaVu читается от
5 мм, жирный — от 4; глубина тиснения — от трёх слоёв (0.6 мм). Тонкое
поймает `min_wall`.

ЗАЛИВКА — по правилу ненулевой обмотки, как у самих шрифтов: контуры
разбиваются на грани, и грань закрашена, если число оборотов контуров вокруг
неё не ноль. Чётность («каждый контур — дырка в предыдущем») дала бы дыры там,
где у шрифта перекрываются контуры одного знака.

НАБОР — HarfBuzz: знаки встают по таблицам шрифта, с кернингом (пары
вроде «AV», «Г.», «То» сдвигаются, как их задумал автор шрифта). Сколько
сдвигать, решает шрифт: у встроенного DejaVu Sans пары есть только у
латиницы — кириллица им набирается по ширинам, а, скажем, у Liberation Sans
кернится и она. `kerning=False` — по одним ширинам. Лигатуры (fi, fl)
выключены: на детали каждая буква — своим знаком, и `spacing` разводит именно
буквы, как в разрядке.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

from shapely.affinity import translate
from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize, unary_union

__all__ = ["text_poly", "text_z", "font_path", "font_names", "project_fonts_stamp",
           "font_title", "system_fonts", "FONT_EXTENSIONS",
           "BUNDLED_FONTS", "DEFAULT_FONT", "FontError"]

BUNDLED_FONTS = Path(__file__).resolve().parent.parent / "assets" / "fonts"
DEFAULT_FONT = "DejaVu Sans"
FONT_EXTENSIONS = (".ttf", ".otf")


class FontError(ValueError):
    """Шрифт не найден или не читается."""


# ---------------------------------------------------------------- шрифты
def font_title(path: Path) -> str:
    """Имя шрифта, как его называют: «DejaVu Sans», «DejaVu Sans Bold»."""
    from fontTools.ttLib import TTFont

    with TTFont(str(path), lazy=True) as font:
        names = font["name"]
        family = (names.getDebugName(16) or names.getDebugName(1) or path.stem).strip()
        style = (names.getDebugName(17) or names.getDebugName(2) or "").strip()
    return family if style.lower() in ("", "regular", "book", "normal", "roman") else f"{family} {style}"


def project_fonts_stamp(root: str | Path) -> str:
    """Отпечаток шрифтов проекта (`fonts/`) — в ключ кеша сборки; нет шрифтов — ""."""
    import hashlib

    folder = Path(root) / "fonts"
    files = sorted(p for p in folder.glob("*") if p.suffix.lower() in FONT_EXTENSIONS) \
        if folder.is_dir() else []
    if not files:
        return ""
    h = hashlib.sha256()
    for p in files:
        h.update(p.name.encode("utf-8") + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()[:16]


def _folders(root: str | Path | None) -> list[Path]:
    out = []
    if root is not None:
        out.append(Path(root) / "fonts")
    out.append(BUNDLED_FONTS)
    return out


@lru_cache(maxsize=64)
def _index(folder: str, stamp: tuple) -> dict[str, Path]:
    found: dict[str, Path] = {}
    base = Path(folder)
    for path in sorted(base.iterdir()) if base.is_dir() else []:
        if path.suffix.lower() in FONT_EXTENSIONS:
            try:
                found.setdefault(font_title(path).lower(), path)
            except Exception:                    # нечитаемый файл — не шрифт
                continue
            found.setdefault(path.stem.lower(), path)
    return found


def _folder_index(folder: Path) -> dict[str, Path]:
    stamp = tuple(sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size)
                         for p in folder.iterdir())) if folder.is_dir() else ()
    return _index(str(folder), stamp)


def font_names(root: str | Path | None = None) -> dict[str, list[str]]:
    """Какие шрифты доступны скрипту детали: {"проект": […], "встроенные": […]}."""
    out = {}
    for label, folder in zip(("проект", "встроенные") if root is not None else ("встроенные",),
                             _folders(root)):
        titles = set()
        for path in sorted(folder.iterdir()) if folder.is_dir() else []:
            if path.suffix.lower() in FONT_EXTENSIONS:
                try:
                    titles.add(font_title(path))
                except Exception:
                    continue
        out[label] = sorted(titles)
    return out


#: Где искать системные шрифты, если нет fc-list.
SYSTEM_FONT_DIRS = ("/usr/share/fonts", "/usr/local/share/fonts", "~/.local/share/fonts", "~/.fonts")


def system_fonts() -> dict[str, Path]:
    """Шрифты машины (только .ttf и .otf): {имя: файл}. Имя — как у `font_title`,
    то есть то, по которому шрифт найдётся, когда `font_add` положит его в проект."""
    import shutil
    import subprocess

    files: list[Path] = []
    if shutil.which("fc-list"):
        try:
            out = subprocess.run(["fc-list", "--format", "%{file}\n"], capture_output=True,
                                 text=True, timeout=30).stdout
            files = [Path(line) for line in out.splitlines() if line.strip()]
        except (OSError, subprocess.SubprocessError):
            files = []
    if not files:
        for base in SYSTEM_FONT_DIRS:
            folder = Path(base).expanduser()
            if folder.is_dir():
                files += [p for p in folder.rglob("*") if p.is_file()]
    found: dict[str, Path] = {}
    for path in sorted(set(files)):
        if path.suffix.lower() not in FONT_EXTENSIONS:
            continue
        try:
            found.setdefault(font_title(path), path)
        except Exception:                        # битый файл — не шрифт
            continue
    return dict(sorted(found.items()))


def _context_root() -> Path | None:
    try:
        from datum.build.context import project_root

        return project_root()
    except Exception:
        return None


def font_path(name: str = DEFAULT_FONT, root: str | Path | None = None) -> Path:
    """Файл шрифта по имени: из `fonts/` проекта, затем встроенный.

    Путь к файлу не принимается намеренно: скрипт детали, читающий шрифт с
    диска машины, собрался бы у другого человека иначе. Системный шрифт —
    операцией `font_add`, она кладёт его в проект.
    """
    root = root if root is not None else _context_root()
    key = str(name).strip().lower()
    for folder in _folders(root):
        hit = _folder_index(folder).get(key)
        if hit is not None:
            return hit
    have = font_names(root)
    listed = "; ".join(f"{k}: {', '.join(v) or '—'}" for k, v in have.items())
    raise FontError(f"шрифт {name!r} не найден. Доступны — {listed}. Системный шрифт "
                    f"добавляется в проект операцией font_add")


# ---------------------------------------------------------------- контуры
class _Flatten:
    """Перо fontTools: контуры глифа ломаными, кривые — с прогибом не больше tol."""

    def __init__(self, glyphset, tol: float):
        from fontTools.pens.basePen import BasePen

        outer = self

        class Pen(BasePen):
            def _moveTo(self, p):
                outer.contours.append([p])

            def _lineTo(self, p):
                outer.contours[-1].append(p)

            def _curveToOne(self, p1, p2, p3):
                p0 = outer.contours[-1][-1]
                n = outer._steps(p0, p1, p2, p3)
                for k in range(1, n + 1):
                    t = k / n
                    a = (1 - t) ** 3
                    b = 3 * (1 - t) ** 2 * t
                    c = 3 * (1 - t) * t * t
                    e = t ** 3
                    outer.contours[-1].append((a * p0[0] + b * p1[0] + c * p2[0] + e * p3[0],
                                               a * p0[1] + b * p1[1] + c * p2[1] + e * p3[1]))

            def _qCurveToOne(self, p1, p2):
                p0 = outer.contours[-1][-1]
                n = outer._steps(p0, p1, p2)
                for k in range(1, n + 1):
                    t = k / n
                    outer.contours[-1].append(((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
                                               (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]))

            def _closePath(self):
                pass

            def _endPath(self):
                pass

        self.contours: list[list[tuple[float, float]]] = []
        self.tol = tol
        self.pen = Pen(glyphset)

    def _steps(self, *pts) -> int:
        # Длина ломаной по опорным точкам — верхняя оценка длины кривой;
        # прогиб хорды ~ L²/(8R), берём с запасом по числу отрезков.
        length = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
        return max(2, min(64, math.ceil(math.sqrt(length / max(self.tol, 1e-9)))))


def _nonzero_fill(contours: list[list[tuple[float, float]]]):
    """Контуры знака → залитая область по правилу ненулевой обмотки."""
    rings = [c for c in contours if len(c) >= 3]
    if not rings:
        return Polygon()
    lines = [LineString(list(c) + [c[0]]) for c in rings]
    faces = list(polygonize(unary_union(lines)))
    keep = []
    for face in faces:
        p = face.representative_point()
        winding = 0
        for c in rings:
            winding += _winding(c, p.x, p.y)
        if winding != 0:
            keep.append(face)
    return unary_union(keep) if keep else Polygon()


def _winding(ring, x: float, y: float) -> int:
    """Число оборотов замкнутой ломаной вокруг точки (со знаком)."""
    w = 0
    n = len(ring)
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        if y0 <= y:
            if y1 > y and (x1 - x0) * (y - y0) - (x - x0) * (y1 - y0) > 0:
                w += 1
        elif y1 <= y and (x1 - x0) * (y - y0) - (x - x0) * (y1 - y0) < 0:
            w -= 1
    return w


@lru_cache(maxsize=16)
def _font(path: str):
    from fontTools.ttLib import TTFont

    return TTFont(path)


@lru_cache(maxsize=16)
def _shaper(path: str):
    import uharfbuzz as hb

    return hb.Font(hb.Face(hb.Blob.from_file_path(path)))


#: Признаки набора: кернинг — по выбору, лигатуры — выключены (см. заголовок).
_NO_LIGATURES = {"liga": False, "clig": False, "dlig": False, "hlig": False}


def _shape(path: str, line: str, kerning: bool) -> list[tuple[int, int, int, int]]:
    """Строка → [(номер знака, сдвиг пера, смещение x, смещение y)] в единицах
    шрифта. Номера знаков — те же, что у fontTools (`getGlyphOrder`)."""
    import uharfbuzz as hb

    buf = hb.Buffer()
    buf.add_str(line)
    buf.guess_segment_properties()
    hb.shape(_shaper(path), buf, {**_NO_LIGATURES, "kern": kerning})
    return [(info.codepoint, pos.x_advance, pos.x_offset, pos.y_offset)
            for info, pos in zip(buf.glyph_infos, buf.glyph_positions)]


def text_poly(s: str, size: float, *, font: str = DEFAULT_FONT, halign: str = "center",
              valign: str = "center", spacing: float = 0.0, line_gap: float = 0.4,
              kerning: bool = True):
    """Надпись плоским профилем: высота ЗАГЛАВНОЙ буквы `size` мм.

    halign: left | center | right — от x = 0; valign: baseline — базовая линия
    первой строки на y = 0, bottom | center | top — по границам самих букв.
    `spacing` — добавка между знаками, мм; `line_gap` — между строками, в долях
    `size`. Переводы строк — «\\n». Знак, которого нет в шрифте, — отказ: тихий
    пропуск буквы на детали заметили бы только после печати. `kerning` — пары
    кернинга шрифта (набор HarfBuzz); False — по одним ширинам знаков.
    """
    if size <= 0:
        raise ValueError(f"text_poly: высота букв положительна ({size:g})")
    if halign not in ("left", "center", "right") or valign not in ("baseline", "bottom",
                                                                   "center", "top"):
        raise ValueError(f"text_poly: halign left|center|right, valign baseline|bottom|center|top "
                         f"(получено {halign!r}, {valign!r})")
    path = font_path(font)
    tt = _font(str(path))
    cmap = tt.getBestCmap()
    glyphs = tt.getGlyphSet()
    missing = sorted({ch for ch in s if ch not in "\n" and not ch.isspace() and ord(ch) not in cmap})
    if missing:
        raise FontError(f"в шрифте {font!r} нет знаков: {''.join(missing)!r}")
    cap = getattr(tt.get("OS/2"), "sCapHeight", 0) or 0
    if cap <= 0:                                   # старые шрифты: меряем «H»
        probe = _Flatten(glyphs, 1.0)
        glyphs[cmap[ord("H")]].draw(probe.pen)
        cap = max(y for c in probe.contours for _, y in c)
    scale = size / cap
    tol = 0.01 / scale                             # прогиб кривой не больше 0.01 мм
    space = glyphs[cmap[ord(" ")]].width if ord(" ") in cmap else tt["head"].unitsPerEm // 3
    order = tt.getGlyphOrder()

    lines_out = []
    for row, line in enumerate(s.split("\n")):
        pen_x = 0.0
        shapes = []
        # Пробелы — свои: любой пробельный знак шириной пробела шрифта (так и
        # было), а слова набираются HarfBuzz — с парами внутри слова.
        for k, word in enumerate(_words(line)):
            if word.isspace():
                pen_x += len(word) * (space * scale + spacing)
                continue
            kerned = _shape(str(path), word, kerning)
            plain = _shape(str(path), word, False) if kerning else kerned
            if [g for g, *_ in plain] != [g for g, *_ in kerned]:    # pragma: no cover
                raise FontError(f"набор {word!r} шрифтом {font!r} меняет знаки при кернинге")
            natural = None              # где встал бы знак без пары кернинга
            for (gid, advance, dx, dy), (_, plain_advance, _, _) in zip(kerned, plain):
                if gid == 0:                       # pragma: no cover — cmap проверен выше
                    raise FontError(f"в шрифте {font!r} нет знака для {word!r}")
                glyph = glyphs[order[gid]]
                flat = _Flatten(glyphs, tol)
                glyph.draw(flat.pen)
                local = _nonzero_fill([[(dx * scale + x * scale, dy * scale + y * scale)
                                        for x, y in c] for c in flat.contours])
                if natural is not None and pen_x < natural and not local.is_empty:
                    pen_x = _kern_floor(local, shapes[-3:], pen_x, natural)
                if not local.is_empty:
                    shapes.append(translate(local, pen_x, 0))
                natural = pen_x + plain_advance * scale + (spacing if advance else 0.0)
                pen_x += advance * scale + (spacing if advance else 0.0)
        width = max(pen_x - spacing, 0.0)
        shift = {"left": 0.0, "center": -width / 2, "right": -width}[halign]
        dy = -row * size * (1 + line_gap)
        lines_out += [translate(g, shift, dy) for g in shapes]
    out = unary_union(lines_out) if lines_out else Polygon()
    if out.is_empty:
        return out
    if valign != "baseline":
        _, miny, _, maxy = out.bounds
        dy = {"bottom": -miny, "top": -maxy, "center": -(miny + maxy) / 2}[valign]
        out = translate(out, 0, dy)
    return out.buffer(0)


#: Ближе этого кернинг букв не сводит. Это запас печати, а не свойство
#: слайсера: нарезка ElegooSlicer (сопло 0.4, линия 0.42) сохраняет в путях
#: щели от 0.2 мм до 0.005 мм точно (замерено), но настоящая линия пластика
#: шире и неровнее расчётной, и щель уже полутора линий между выпуклыми
#: буквами рискует затянуться — печатью это не проверено. Кернинг шрифта у
#: «To», «LT» на 4 мм давал 0.44 мм; пара, тесная и без кернинга («Ty»
#: жирным — 0.24 мм), остаётся как была: кернинг её не трогает.
KERN_MIN_GAP_MM = 0.6


def _kern_floor(glyph, before: list, x: float, natural: float) -> float:
    """Положение знака с кернингом, но не теснее предела.

    Кернинг сдвигает знак влево от `natural` (где он стоял бы по ширинам) к
    `x`. Если щель с соседями стала меньше min(щель без кернинга,
    KERN_MIN_GAP_MM) — знак отодвигается обратно ровно до неё (бисекцией по
    сдвигу: щель растёт, пока знак едет вправо)."""
    if not before:
        return x

    def gap(at: float) -> float:
        moved = translate(glyph, at, 0)
        return min(moved.distance(b) for b in before)

    floor = min(gap(natural), KERN_MIN_GAP_MM)
    if gap(x) >= floor:
        return x
    lo, hi = x, natural                 # в lo щель мала, в hi — достаточна
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if gap(mid) < floor else (lo, mid)
    return hi


def _words(line: str) -> list[str]:
    """Строка — чередованием слов и пробельных промежутков."""
    import re

    return [m.group(0) for m in re.finditer(r"\s+|\S+", line)]


def text_z(s: str, size: float, z0: float, z1: float, x: float = 0.0, y: float = 0.0, *,
           font: str = DEFAULT_FONT, angle: float = 0.0, halign: str = "center",
           valign: str = "center", spacing: float = 0.0, kerning: bool = True,
           name: str = ""):
    """Надпись телом: профиль `text_poly`, повёрнутый на `angle`° вокруг (x, y)
    и выдавленный от z0 до z1. Прибавить к детали — выпуклая, вычесть — вдавленная.
    На боковую грань — `rotate` готового тела."""
    from shapely.affinity import rotate

    from datum.geom.prims import extrude

    poly = text_poly(s, size, font=font, halign=halign, valign=valign, spacing=spacing,
                     kerning=kerning)
    poly = translate(rotate(poly, angle, origin=(0, 0)), x, y)
    return extrude(poly, z0, z1, name=name)

