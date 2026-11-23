"""Gridfinity: ящики и плиты под сетку 42 мм.

    from datum.std.gridfinity import Bin, baseplate

    ящик = Bin(3, 2, 6)                      # 3×2 ячейки, 6 единиц высоты
    ящик.pocket(88.6, 72.6, depth=20, corner_r=4)
    return ящик.solid()

Ящик стоит по центру начала координат, низ на Z=0 — так и печатается
(`print_up="+Z"`).

КОНСТАНТЫ СВЕРЕНЫ С ЭТАЛОНОМ, а не с чужим пересказом: gridfinity-rebuilt-
openscad (MIT, `src/core/standard.scad`), который следует спецификации
gridfinity.xyz. Сверка нашла расхождение в навыке cad-skill, с которого
началась эта работа: там губка для стопки повторяет профиль основания
(0.8/1.8/2.15), а по спецификации она 0.7/1.8/1.9 — ящик сверху садился бы
не на ту высоту.

ДВА ОСОЗНАННЫХ ОТСТУПЛЕНИЯ, оба ради печати:
  * вершина губки — не лезвие нулевой ширины, а полка 0.4 мм (один проход
    сопла): губка кончается на 4.0 мм над номиналом, а не на 4.4. Эталон
    решает то же скруглением 0.6 мм — оно тоже срезает остриё;
  * стенка по умолчанию 1.6 мм, а не 0.95 эталона: у мастерской правило «стенки
    от 1.6 мм» (четыре периметра сопла 0.4). Меньше — можно, но явно.

Ошибки параметров — `GridfinityError` с названной причиной: карман шире
внутренности, глубже дна, отсеки вместе с карманами. Обходить их не надо — они
и есть ответ. Пересекающиеся карманы — не ошибка: они сливаются в один.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from datum.geom.booleans import compose, diff, hull, union
from datum.geom.prims import cyl_z, extrude
from datum.geom.profile2d import rrect_poly
from datum.geom.solid import Solid
from datum.geom.xform import move

__all__ = ["GridfinityError", "Bin", "baseplate", "PITCH", "UNIT", "BASE_HEIGHT",
           "LIP_HEIGHT", "BIN_CLEARANCE"]

# ------------------------------------------------------------ спецификация, мм
PITCH = 42.0                 # шаг сетки
UNIT = 7.0                   # единица высоты
BIN_CLEARANCE = 0.25         # на сторону: ячейка ящика 41.5
CORNER_R = 3.75              # скругление ящика в плане (7.5/2)
BASE_PROFILE = (0.8, 1.8, 2.15)      # 45°, вертикаль, 45° — высота 4.75
BASE_PROFILE_H = sum(BASE_PROFILE)
BASE_HEIGHT = 7.0            # основание вместе с мостом, связывающим ячейки
LIP_PROFILE = (0.7, 1.8, 1.9)        # губка для стопки, от внутреннего края
LIP_SUPPORT = 1.2            # вертикаль под губкой до опорной фаски 45°
LIP_TIP = 0.4                # полка на вершине губки (см. отступления выше)
LIP_HEIGHT = sum(LIP_PROFILE) - LIP_TIP      # 4.0 — насколько губка выше номинала
MAGNET_D, MAGNET_DEPTH = 6.5, 2.4    # под магнит 6×2 с запасом в два слоя
SCREW_D, SCREW_DEPTH = 3.0, 6.0      # M3
HOLE_OFFSET = 13.0           # центры отверстий: квадрат 26 мм в ячейке
PLATE_HEIGHT = 5.0           # плита
PLATE_PROFILE = (0.7, 1.8, 2.15)     # гнездо плиты
PLATE_CORNER_R = 4.0         # внешнее скругление плиты (8/2)

# ----------------------------------------------------------- печатность, мм
DEFAULT_WALL = 1.6
MIN_WALL = 0.95              # тоньше эталона не бывает
DIVIDER = 1.2                # перегородка между отсеками
MIN_FLOOR = 1.0              # под карманом до основания
_EPS = 1e-3


class GridfinityError(ValueError):
    """Параметры ящика несовместимы со спецификацией или друг с другом."""


def _rr(w: float, d: float, r: float, cx: float = 0.0, cy: float = 0.0):
    return rrect_poly(cx - w / 2, cy - d / 2, cx + w / 2, cy + d / 2, max(r, 0.0))


def _frustum(lower, z0: float, upper, z1: float) -> Solid:
    """Тело между двумя сечениями — выпуклая оболочка тонких плит.

    Смещение скруглённого прямоугольника внутрь меняет и стороны, и радиус
    линейно, поэтому оболочка двух таких сечений — точный скос 45°, а не
    лестница из слоёв.
    """
    return hull(compose([extrude(lower, z0, z0 + _EPS), extrude(upper, z1 - _EPS, z1)]))


def _inset_rr(w: float, d: float, r: float, inset: float, cx=0.0, cy=0.0):
    return _rr(w - 2 * inset, d - 2 * inset, r - inset, cx, cy)


@dataclass
class _Pocket:
    shape: object            # shapely в координатах ящика
    depth: float


@dataclass
class Bin:
    """Ящик Gridfinity: `nx`×`ny` ячеек, `units` единиц высоты по 7 мм.

    Высота считается ВМЕСТЕ с основанием и БЕЗ губки: ящик в 6 единиц — 42 мм
    плюс губка. Внутри ящик полый (`compartments` делят его перегородками) или
    сплошной с карманами (`pocket`, `shape_pocket`, `round_pocket`) — вместе
    нельзя: перегородки в сплошном теле ничего не делят.
    """

    nx: int
    ny: int
    units: int
    lip: bool = True
    magnets: bool = False
    screws: bool = False
    wall: float = DEFAULT_WALL
    _pockets: list = field(default_factory=list, repr=False)
    _compartments: tuple | None = field(default=None, repr=False)

    def __post_init__(self):
        for name in ("nx", "ny", "units"):
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool) or v < 1:
                raise GridfinityError(f"{name} — целое число от 1, получено {v!r}")
        if self.units * UNIT <= BASE_HEIGHT:
            raise GridfinityError(f"ящик в {self.units} ед. = {self.units * UNIT:g} мм "
                                  f"не выше основания {BASE_HEIGHT:g} мм: места нет")
        max_wall = LIP_PROFILE[0] + LIP_PROFILE[2] - 0.1
        if not (MIN_WALL <= self.wall <= max_wall):
            raise GridfinityError(f"стенка {self.wall:g} мм вне [{MIN_WALL}, {max_wall:g}]: "
                                  f"тоньше эталона не печатают, а толще вылета губки "
                                  f"(2.6 мм) губке не на что опереться")

    # ------------------------------------------------------------ размеры
    @property
    def outer_x(self) -> float:
        return self.nx * PITCH - 2 * BIN_CLEARANCE

    @property
    def outer_y(self) -> float:
        return self.ny * PITCH - 2 * BIN_CLEARANCE

    @property
    def height(self) -> float:
        """Номинальная высота: с основанием, без губки."""
        return self.units * UNIT

    @property
    def total_height(self) -> float:
        return self.height + (LIP_HEIGHT if self.lip else 0.0)

    @property
    def floor_z(self) -> float:
        """Дно полого ящика: верх основания."""
        return BASE_HEIGHT

    @property
    def max_depth(self) -> float:
        """Глубже карман уйти не может: под ним остаётся MIN_FLOOR над профилем
        основания, а в нём сидят магниты и винты."""
        return self.height - BASE_PROFILE_H - MIN_FLOOR

    @property
    def inner_x(self) -> float:
        return self.outer_x - 2 * self.wall

    @property
    def inner_y(self) -> float:
        return self.outer_y - 2 * self.wall

    def summary(self) -> str:
        return (f"ящик {self.nx}×{self.ny}×{self.units} ед.: снаружи {self.outer_x:g}×"
                f"{self.outer_y:g} мм, высота {self.height:g} мм"
                f"{f' + губка {LIP_HEIGHT:g}' if self.lip else ''}, внутри "
                f"{self.inner_x:g}×{self.inner_y:g} мм от {self.floor_z:g} мм, "
                f"карман до {self.max_depth:g} мм глубиной")

    # ------------------------------------------------------------ наполнение
    def _check_pocket(self, shape, depth: float, what: str) -> None:
        from shapely.geometry import box as sbox

        if self._compartments is not None:
            raise GridfinityError(f"{what}: у ящика уже есть отсеки — карманы режутся "
                                  f"в сплошном ящике, перегородки делят полый")
        if not (0 < depth <= self.max_depth + 1e-9):
            raise GridfinityError(f"{what}: глубина {depth:g} мм вне (0, {self.max_depth:g}] — "
                                  f"под карманом должно остаться {MIN_FLOOR:g} мм над "
                                  f"основанием")
        inner = sbox(-self.inner_x / 2, -self.inner_y / 2, self.inner_x / 2, self.inner_y / 2)
        if not inner.contains(shape):
            x0, y0, x1, y1 = shape.bounds
            raise GridfinityError(
                f"{what}: карман {x1 - x0:.2f}×{y1 - y0:.2f} мм выходит за стенку "
                f"{self.wall:g} мм — внутри есть {self.inner_x:g}×{self.inner_y:g}")

    def pocket(self, length: float, width: float, *, depth: float | None = None,
               corner_r: float = 1.0, center: tuple[float, float] = (0.0, 0.0)) -> "Bin":
        """Прямоугольный карман со скруглёнными углами, от верха ящика вниз."""
        depth = self.max_depth if depth is None else depth
        shape = _rr(length, width, corner_r, *center)
        self._check_pocket(shape, depth, f"карман {length:g}×{width:g}")
        self._pockets.append(_Pocket(shape, depth))
        return self

    def shape_pocket(self, shape, *, depth: float | None = None) -> "Bin":
        """Карман по контуру — например, `footprint(...)` предмета по скану."""
        depth = self.max_depth if depth is None else depth
        self._check_pocket(shape, depth, "карман по контуру")
        self._pockets.append(_Pocket(shape, depth))
        return self

    def round_pocket(self, diameter: float, *, depth: float | None = None,
                     center: tuple[float, float] = (0.0, 0.0)) -> "Bin":
        """Круглый карман — под банку, колпачок, выемку под палец."""
        from shapely.geometry import Point

        depth = self.max_depth if depth is None else depth
        shape = Point(center).buffer(diameter / 2, quad_segs=32)
        self._check_pocket(shape, depth, f"карман ⌀{diameter:g}")
        self._pockets.append(_Pocket(shape, depth))
        return self

    def compartments(self, cols: int, rows: int = 1, *, divider: float = DIVIDER) -> "Bin":
        """Разделить полый ящик на cols×rows отсеков перегородками."""
        if self._pockets:
            raise GridfinityError("отсеки делят полый ящик, а у этого уже есть карманы")
        if cols < 1 or rows < 1:
            raise GridfinityError(f"отсеков {cols}×{rows}: нужно хотя бы по одному")
        if divider < MIN_WALL:
            raise GridfinityError(f"перегородка {divider:g} мм тоньше {MIN_WALL} — не напечатать")
        cell_x = (self.inner_x - (cols - 1) * divider) / cols
        cell_y = (self.inner_y - (rows - 1) * divider) / rows
        if min(cell_x, cell_y) < 5.0:
            raise GridfinityError(f"отсек {cell_x:.1f}×{cell_y:.1f} мм — уже 5 мм, "
                                  f"в него ничего не положить")
        self._compartments = (cols, rows, divider)
        return self

    # ------------------------------------------------------------ сборка
    def _base(self) -> Solid:
        cell = PITCH - 2 * BIN_CLEARANCE
        t1, riser, t2 = BASE_PROFILE
        bottom = _inset_rr(cell, cell, CORNER_R, t1 + t2)
        mid = _inset_rr(cell, cell, CORNER_R, t2)
        top = _rr(cell, cell, CORNER_R)
        one = union([_frustum(bottom, 0.0, mid, t1),
                     extrude(mid, t1, t1 + riser),
                     _frustum(mid, t1 + riser, top, BASE_PROFILE_H)])
        cells = [move(one, dx=(i - (self.nx - 1) / 2) * PITCH, dy=(j - (self.ny - 1) / 2) * PITCH)
                 for i in range(self.nx) for j in range(self.ny)]
        # Мост от верха профиля до BASE_HEIGHT — сплошной по всему ящику: он
        # и связывает ячейки в одно тело.
        return union(cells)

    def _holes(self) -> list[Solid]:
        out = []
        for i in range(self.nx):
            for j in range(self.ny):
                cx, cy = (i - (self.nx - 1) / 2) * PITCH, (j - (self.ny - 1) / 2) * PITCH
                for sx in (-1, 1):
                    for sy in (-1, 1):
                        x, y = cx + sx * HOLE_OFFSET, cy + sy * HOLE_OFFSET
                        if self.magnets:
                            out.append(cyl_z(MAGNET_D, -1.0, MAGNET_DEPTH, x=x, y=y, n=48))
                        if self.screws:
                            out.append(cyl_z(SCREW_D, -1.0, SCREW_DEPTH, x=x, y=y, n=32))
        return out

    def _lip_cut(self, hollow: bool) -> Solid:
        """Полость губки до открытого верха.

        У полого ящика губка висит над пустотой, и под ней опорная фаска 45°
        до стенки. У сплошного под губкой материал: срез начинается прямо от
        номинальной высоты, иначе он съел бы 2 мм верха вокруг карманов.
        """
        w, d, h = self.outer_x, self.outer_y, self.height
        a, riser, b = LIP_PROFILE
        tip = a + b                            # 2.6 — вылет губки внутрь от стенки
        support_z = h - LIP_SUPPORT - (tip - self.wall)
        below = [(support_z, self.wall),       # стенка встречает опорную фаску 45°
                 (h - LIP_SUPPORT, tip)] if hollow else []
        levels = below + [
            (h, tip),
            (h + a, tip - a),
            (h + a + riser, tip - a),
            (self.total_height, LIP_TIP),
        ]
        pieces = []
        for (z0, i0), (z1, i1) in zip(levels, levels[1:]):
            lower = _inset_rr(w, d, CORNER_R, i0)
            upper = _inset_rr(w, d, CORNER_R, i1)
            pieces.append(extrude(lower, z0, z1) if i0 == i1 else _frustum(lower, z0, upper, z1))
        # Выше вершины — пусто, чтобы разность не оставила плёнку.
        pieces.append(extrude(_inset_rr(w, d, CORNER_R, LIP_TIP),
                              self.total_height - _EPS, self.total_height + 1.0))
        return union(pieces)

    def solid(self, *, name: str = "") -> Solid:
        w, d, h = self.outer_x, self.outer_y, self.height
        body = union([self._base(),
                      extrude(_rr(w, d, CORNER_R), BASE_PROFILE_H, self.total_height)])
        cuts: list[Solid] = []
        if self.lip:
            cuts.append(self._lip_cut(hollow=not self._pockets))
        top = h if self.lip else h + 1.0
        if self._pockets:
            for p in self._pockets:
                cuts.append(extrude(p.shape, h - p.depth, top + LIP_HEIGHT + 1.0))
        else:
            cavity_top = h - LIP_SUPPORT - (LIP_PROFILE[0] + LIP_PROFILE[2] - self.wall) \
                if self.lip else h + 1.0
            cavity = extrude(_inset_rr(w, d, CORNER_R, self.wall), self.floor_z,
                             cavity_top + _EPS)
            if self._compartments is not None:
                cols, rows, div = self._compartments
                walls = []
                for k in range(1, cols):
                    x = -self.inner_x / 2 + k * (self.inner_x + div) / cols - div / 2
                    walls.append(extrude(_rr(div, self.inner_y + 1, 0, x, 0),
                                         self.floor_z - _EPS, h))
                for k in range(1, rows):
                    y = -self.inner_y / 2 + k * (self.inner_y + div) / rows - div / 2
                    walls.append(extrude(_rr(self.inner_x + 1, div, 0, 0, y),
                                         self.floor_z - _EPS, h))
                cavity = diff(cavity, walls) if walls else cavity
            cuts.append(cavity)
        cuts += self._holes()
        result = diff(body, cuts)
        return result.renamed(name) if name else result


def baseplate(nx: int, ny: int, *, name: str = "") -> Solid:
    """Плита под сетку: nx×ny гнёзд, высота 5 мм, дно открыто.

    Гнездо — профиль 0.7/1.8/2.15 от ячейки 42 мм со скруглением 4 мм; под
    ним сквозное окно. Между гнёздами стенки сходятся в ребро — так в
    спецификации, и именно это ребро держит ящик на месте.
    """
    for label, v in (("nx", nx), ("ny", ny)):
        if not isinstance(v, int) or isinstance(v, bool) or v < 1:
            raise GridfinityError(f"{label} — целое число от 1, получено {v!r}")
    t1, riser, t2 = PLATE_PROFILE
    z0 = PLATE_HEIGHT - sum(PLATE_PROFILE)
    body = extrude(_rr(nx * PITCH, ny * PITCH, PLATE_CORNER_R), 0.0, PLATE_HEIGHT)
    bottom = _inset_rr(PITCH, PITCH, PLATE_CORNER_R, t1 + t2)
    mid = _inset_rr(PITCH, PITCH, PLATE_CORNER_R, t2)
    top = _rr(PITCH, PITCH, PLATE_CORNER_R)
    socket = union([extrude(bottom, -1.0, z0 + _EPS),
                    _frustum(bottom, z0, mid, z0 + t1),
                    extrude(mid, z0 + t1, z0 + t1 + riser),
                    _frustum(mid, z0 + t1 + riser, top, PLATE_HEIGHT),
                    extrude(top, PLATE_HEIGHT - _EPS, PLATE_HEIGHT + 1.0)])
    sockets = [move(socket, dx=(i - (nx - 1) / 2) * PITCH, dy=(j - (ny - 1) / 2) * PITCH)
               for i in range(nx) for j in range(ny)]
    result = diff(body, sockets)
    return result.renamed(name) if name else result
