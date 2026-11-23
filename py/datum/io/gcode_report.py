"""Что слайсер СДЕЛАЛ с деталью: разбор тела G-code по слоям.

Откуда. Слайсер — единственный, кто знает, как деталь будет напечатана, а
Datum читал у него только подвал (конфиг, время, граммы). В Gear Cube агент
разбирал тело G-code руками в 23 командах, и «лепесток висит в воздухе» нашёл
не он, а человек, глядя в окно слайсера (аудит 2026-11-21, §7.3).

Что считается — только то, что в файле записано, без догадок о печати:

* **острова в воздухе** — кусок слоя, под которым в предыдущем слое нет НИЧЕГО
  (ни детали, ни поддержки): пластик выдавлен в пустоту. Это местный минимум
  детали не на столе; с поддержками его нет по построению;
* **нависающие стенки** — линии, которые слайсер сам пометил `Overhang wall`,
  с длинами по слоям;
* **мосты** — линии `Bridge` (наружные; `Internal Bridge` — крышка над
  заполнением, дефектом не бывает) и самая длинная прямая из них;
* **первый слой** — площадь пластика детали на столе и каймы отдельно;
* **пластик по видам линий** — стенки, заполнение, поддержки, кайма, граммы.

Времени по видам линий нет: его в G-code не пишут, а считать его самим —
значит моделировать ускорения принтера.

Координаты — стола, в позе печати: так деталь лежит в `slice`/`export` и в
окне слайсера.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from datum.io.slicer import NOT_PART, SlicerError

__all__ = ["GcodeReport", "Island", "Run", "analyse", "CELL_MM"]

#: Клетка растра слоя, мм: половина линии сопла 0.4 — соседние линии (шаг
#: ~0.45 мм) после раздувания на клетку срастаются, а щель в линию остаётся.
CELL_MM = 0.2
#: Остров меньше этого (мм² пластика) — крупинка заполнения, а не элемент.
ISLAND_MIN_MM2 = 0.3
#: На сколько клеток раздувается предыдущий слой, когда ищется опора: линия
#: слоя выше законно сдвинута на долю ширины (стенка под 45° при слое 0.2 —
#: на 0.2 мм); остров — это когда рядом нет ничего и на ТАКОМ расстоянии.
SUPPORT_REACH_CELLS = 3
#: Поддержка держит и через зазор: слайсер оставляет между её верхом и деталью
#: щель (support_top_z_distance, обычно слой-два), чтобы поддержка отламывалась.
#: Опорой считается поддержка не ниже этого под низом слоя, мм.
SUPPORT_GAP_MM = 0.45
#: Линии поддержек: опора для слоя выше, но не деталь.
SUPPORT_KINDS = frozenset({"Support", "Support interface", "Support transition"})
#: Что не лежит под деталью опорой вовсе (прочистка, башня — в стороне).
NO_SUPPORT_KINDS = frozenset({"Custom", "Prime tower", "Wipe tower"})


@dataclass(frozen=True)
class Island:
    """Кусок слоя, под которым пусто."""
    layer: int                       # номер слоя, с 1
    z: float                         # высота верха слоя над столом, мм
    area_mm2: float                  # пластика в куске (длина × ширина линий)
    centre: tuple[float, float]      # на столе, мм
    size: tuple[float, float]        # габарит куска по X и Y, мм
    kinds: tuple[str, ...]           # какими линиями напечатан


@dataclass(frozen=True)
class Run:
    """Подряд идущие слои с линиями одного вида."""
    first: int
    last: int
    z0: float
    z1: float
    length_mm: float


@dataclass
class GcodeReport:
    layers: int = 0
    height_mm: float = 0.0
    first_layer_mm2: float = 0.0             # деталь на столе
    brim_mm2: float = 0.0                    # кайма и юбка
    by_kind: dict[str, dict[str, float]] = field(default_factory=dict)
    overhang_mm: float = 0.0
    overhang_runs: list[Run] = field(default_factory=list)
    bridge_mm: float = 0.0
    bridge_runs: list[Run] = field(default_factory=list)
    longest_bridge_mm: float = 0.0
    longest_bridge_z: float = 0.0
    islands: list[Island] = field(default_factory=list)
    support_g: float = 0.0
    part_g: float = 0.0
    arcs: int = 0                            # дуги с подачей, считанные хордами

    def lines(self, *, supports_on: bool | None = None, limit: int = 6) -> list[str]:
        """Строки отчёта — без отступа, его ставит тот, кто печатает."""
        out = [f"первый слой: деталь {self.first_layer_mm2:.0f} мм² на столе"
               + (f", кайма/юбка {self.brim_mm2:.0f} мм²" if self.brim_mm2 > 0.5 else
                  ", каймы нет")]
        if self.islands:
            out.append(f"ОСТРОВА В ВОЗДУХЕ — {len(self.islands)}: кусок слоя, под которым "
                       f"нет ни детали, ни поддержки (пластик выдавлен в пустоту)")
            ranked = sorted(self.islands, key=lambda i: -i.area_mm2)
            for i in ranked[:limit]:
                out.append(f"  слой {i.layer} (Z {i.z:.2f}): {i.area_mm2:.1f} мм², "
                           f"{i.size[0]:.1f}×{i.size[1]:.1f} мм у X {i.centre[0]:.1f} "
                           f"Y {i.centre[1]:.1f} на столе")
            if len(ranked) > limit:
                rest = ranked[limit:]
                out.append(f"  … и ещё {len(rest)} поменьше, до "
                           f"{rest[0].area_mm2:.1f} мм² (все — в поле islands)")
            if supports_on is False:
                out.append("  поддержки в профиле выключены: включите их правкой "
                           "(overrides enable_support=1), смените позу печати или "
                           "доведите элемент до опоры в самой детали")
        else:
            out.append("островов в воздухе нет: каждый кусок каждого слоя лежит на "
                       "предыдущем слое или на поддержке")
        out.append(_runs_line("нависающие стенки (Overhang wall слайсера)",
                              self.overhang_mm, self.overhang_runs, limit))
        bridge = _runs_line("мосты (Bridge)", self.bridge_mm, self.bridge_runs, limit)
        if self.bridge_mm > 0:
            bridge += (f"; самая длинная прямая {self.longest_bridge_mm:.1f} мм на "
                       f"Z {self.longest_bridge_z:.2f}")
        out.append(bridge)
        shares = sorted(((k, v["grams"]) for k, v in self.by_kind.items() if v["grams"] > 0.005),
                        key=lambda kv: -kv[1])
        if shares:
            out.append("пластик по линиям: "
                       + ", ".join(f"{_KIND_RU.get(k, k)} {g:.2f} г" for k, g in shares))
        if self.arcs:
            out.append(f"дуги G2/G3 с подачей ({self.arcs}) считаны хордами — длины "
                       f"занижены на доли процента")
        return out

    def as_dict(self) -> dict:
        return {
            "layers": self.layers, "height_mm": round(self.height_mm, 3),
            "first_layer_mm2": round(self.first_layer_mm2, 1),
            "brim_mm2": round(self.brim_mm2, 1),
            "overhang_wall_mm": round(self.overhang_mm, 1),
            "overhang_layers": [[r.first, r.last, round(r.length_mm, 1)]
                                for r in self.overhang_runs],
            "bridge_mm": round(self.bridge_mm, 1),
            "bridge_layers": [[r.first, r.last, round(r.length_mm, 1)]
                              for r in self.bridge_runs],
            "longest_bridge_mm": round(self.longest_bridge_mm, 2),
            "islands": [{"layer": i.layer, "z": round(i.z, 3),
                         "area_mm2": round(i.area_mm2, 2),
                         "centre": [round(c, 2) for c in i.centre],
                         "size": [round(s, 2) for s in i.size]} for i in self.islands],
            "support_g": round(self.support_g, 3), "part_g": round(self.part_g, 3),
            "grams_by_kind": {k: round(v["grams"], 3) for k, v in self.by_kind.items()
                              if v["grams"] > 0.0005},
        }


_KIND_RU = {
    "Outer wall": "наружная стенка", "Inner wall": "внутренние стенки",
    "Overhang wall": "нависающие стенки", "Sparse infill": "заполнение",
    "Internal solid infill": "сплошное заполнение", "Top surface": "верх",
    "Bottom surface": "низ", "Bridge": "мосты", "Internal Bridge": "внутренние мосты",
    "Gap infill": "заполнение щелей", "Support": "поддержки",
    "Support interface": "интерфейс поддержек", "Support transition": "переход поддержек",
    "Brim": "кайма", "Skirt": "юбка", "Custom": "прочистка и служебное",
    "Prime tower": "башня", "Wipe tower": "башня", "Ironing": "глажение",
}


def _runs_line(title: str, total: float, runs: list[Run], limit: int) -> str:
    if total <= 0:
        return f"{title}: нет"
    ranked = sorted(runs, key=lambda r: -r.length_mm)[:limit]
    ranked.sort(key=lambda r: r.first)
    parts = [(f"слой {r.first}" if r.first == r.last else f"слои {r.first}–{r.last}")
             + (f" (Z {r.z1:.2f})" if r.first == r.last else f" (Z {r.z0:.2f}…{r.z1:.2f})")
             + f" {_mm(r.length_mm)}" for r in ranked]
    more = f" и ещё {len(runs) - len(ranked)} участков" if len(runs) > len(ranked) else ""
    return f"{title}: {_mm(total)} — " + "; ".join(parts) + more


def _mm(length: float) -> str:
    return f"{length:.0f} мм" if length >= 10 else f"{length:.1f} мм"


def _runs(per_layer: dict[int, float], z_of: dict[int, tuple[float, float]]) -> list[Run]:
    """Слои с линиями вида — в участки подряд идущих слоёв."""
    runs: list[Run] = []
    group: list[int] = []
    for n in [*sorted(per_layer), None]:
        if group and (n is None or n != group[-1] + 1):
            runs.append(Run(group[0], group[-1], z_of[group[0]][0], z_of[group[-1]][1],
                            sum(per_layer[k] for k in group)))
            group = []
        if n is not None:
            group.append(n)
    return runs


class _Layer:
    """Отрезки одного слоя: x0 y0 x1 y1, ширина и вид (номером)."""
    __slots__ = ("number", "z", "height", "seg", "width", "kind")

    def __init__(self, number: int, z: float, height: float):
        self.number, self.z, self.height = number, z, height
        self.seg: list[tuple[float, float, float, float]] = []
        self.width: list[float] = []
        self.kind: list[int] = []


def _paint(points_ij: np.ndarray, lo: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    mask = np.zeros(shape, dtype=bool)
    if len(points_ij):
        ij = points_ij - lo
        mask[ij[:, 0], ij[:, 1]] = True
    return mask


def _samples(seg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Клетки вдоль отрезков (шаг — полклетки) и номер отрезка каждой точки."""
    d = seg[:, 2:4] - seg[:, 0:2]
    length = np.hypot(d[:, 0], d[:, 1])
    count = np.ceil(length / (CELL_MM / 2)).astype(np.int64) + 1
    owner = np.repeat(np.arange(len(seg)), count)
    first = np.cumsum(count) - count
    t = (np.arange(count.sum()) - first[owner]) / np.maximum(count[owner] - 1, 1)
    xy = seg[owner, 0:2] + d[owner] * t[:, None]
    return np.floor(xy / CELL_MM).astype(np.int64), owner


def analyse(path: Path, *, density: float, diameter: float = 1.75) -> GcodeReport:
    """Разобрать тело G-code. `density` — г/см³ (из подвала того же файла).

    Подача — относительная (M83), как пишет ElegooSlicer; абсолютная — отказ,
    как у `slicer.part_grams`: насчитать по ней вздор хуже, чем не считать.
    """
    from scipy import ndimage

    report = GcodeReport()
    gram_per_mm = math.pi * (diameter / 2) ** 2 * density / 1000.0
    kinds: list[str] = []
    kind_index: dict[str, int] = {}

    def kind_no(name: str) -> int:
        if name not in kind_index:
            kind_index[name] = len(kinds)
            kinds.append(name)
            report.by_kind[name] = {"length_mm": 0.0, "filament_mm": 0.0, "grams": 0.0}
        return kind_index[name]

    overhang_by_layer: dict[int, float] = {}
    bridge_by_layer: dict[int, float] = {}
    z_of: dict[int, tuple[float, float]] = {}
    eight = np.ones((3, 3), dtype=bool)
    below: tuple[np.ndarray, np.ndarray] | None = None     # (угол окна, маска) слоя ниже
    props: list[tuple[float, np.ndarray, np.ndarray]] = []  # (верх, угол, маска) поддержек

    def paste(shape, lo, b_lo, b_mask, into) -> None:
        """Наложить маску другого окна: клетки — в общих целых координатах стола."""
        a0 = np.maximum(lo, b_lo)
        a1 = np.minimum(lo + shape, b_lo + b_mask.shape)
        if (a1 > a0).all():
            into[a0[0] - lo[0]:a1[0] - lo[0], a0[1] - lo[1]:a1[1] - lo[1]] |= \
                b_mask[a0[0] - b_lo[0]:a1[0] - b_lo[0], a0[1] - b_lo[1]:a1[1] - b_lo[1]]

    def finish(layer: _Layer) -> None:
        nonlocal below
        if not layer.seg:
            # Пустой слой — не опора: что бы ни легло выше, оно ляжет в воздух.
            below = (np.zeros(2, dtype=np.int64), np.zeros((1, 1), dtype=bool))
            return
        seg = np.asarray(layer.seg, dtype=float)
        width = np.asarray(layer.width, dtype=float)
        kind = np.asarray(layer.kind, dtype=np.int64)
        length = np.hypot(seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1])
        names = [kinds[k] for k in range(len(kinds))]
        is_part = np.array([n not in NOT_PART for n in names])[kind]
        holds = np.array([n not in NO_SUPPORT_KINDS for n in names])[kind]
        z_of[layer.number] = (layer.z - layer.height, layer.z)
        for k in np.unique(kind):
            name = kinds[k]
            total = float(length[kind == k].sum())
            if name == "Overhang wall":
                overhang_by_layer[layer.number] = total
            elif name == "Bridge":
                bridge_by_layer[layer.number] = total
                longest = float(length[kind == k].max())
                if longest > report.longest_bridge_mm:
                    report.longest_bridge_mm, report.longest_bridge_z = longest, layer.z
        if layer.number == 1:
            report.first_layer_mm2 = float((length * width)[is_part].sum())
            brim = np.array([n in ("Brim", "Skirt") for n in names])[kind]
            report.brim_mm2 = float((length * width)[brim].sum())

        cells, owner = _samples(seg)
        keep = holds[owner]
        if not keep.any():
            below = (np.zeros(2, dtype=np.int64), np.zeros((1, 1), dtype=bool))
            return
        lo = cells[keep].min(axis=0) - (SUPPORT_REACH_CELLS + 2)
        hi = cells[keep].max(axis=0) + (SUPPORT_REACH_CELLS + 3)
        shape = (int(hi[0] - lo[0]), int(hi[1] - lo[1]))
        part_mask = ndimage.binary_dilation(_paint(cells[is_part[owner]], lo, shape), eight)
        all_mask = ndimage.binary_dilation(_paint(cells[keep], lo, shape), eight)
        if below is not None and layer.number > 1 and part_mask.any():
            under = np.zeros(shape, dtype=bool)
            paste(shape, lo, *below, under)
            bottom = layer.z - layer.height
            for top, p_lo, p_mask in props:
                if top >= bottom - SUPPORT_GAP_MM:
                    paste(shape, lo, p_lo, p_mask, under)
            under = ndimage.binary_dilation(under, eight, iterations=SUPPORT_REACH_CELLS)
            labels, count = ndimage.label(part_mask, structure=eight)
            if count:
                held = np.zeros(count + 1, dtype=bool)
                held[np.unique(labels[under])] = True
                held[0] = True
                if not held.all():
                    mid = np.floor((seg[:, 0:2] + seg[:, 2:4]) / 2 / CELL_MM).astype(np.int64) - lo
                    seg_label = np.where(is_part, labels[mid[:, 0], mid[:, 1]], 0)
                    for lab in np.nonzero(~held)[0]:
                        mine = seg_label == lab
                        area = float((length * width)[mine].sum())
                        if area < ISLAND_MIN_MM2:
                            continue
                        pts = np.vstack([seg[mine, 0:2], seg[mine, 2:4]])
                        p_lo, p_hi = pts.min(axis=0), pts.max(axis=0)
                        report.islands.append(Island(
                            layer=layer.number, z=layer.z, area_mm2=area,
                            centre=(float((p_lo[0] + p_hi[0]) / 2), float((p_lo[1] + p_hi[1]) / 2)),
                            size=(float(p_hi[0] - p_lo[0]), float(p_hi[1] - p_lo[1])),
                            kinds=tuple(sorted({kinds[k] for k in np.unique(kind[mine])}))))
        below = (lo, all_mask)
        is_prop = np.array([n in SUPPORT_KINDS for n in names])[kind]
        props[:] = [p for p in props if p[0] >= layer.z - SUPPORT_GAP_MM - 1.0]
        if is_prop.any():
            props.append((layer.z, lo, ndimage.binary_dilation(
                _paint(cells[is_prop[owner]], lo, shape), eight)))

    x = y = 0.0
    absolute_xy = True
    relative_e: bool | None = None
    kind_now = kind_no("Custom")
    width_now = 0.4
    layer: _Layer | None = None
    number = 0
    pending_z: float | None = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            c = line[:1]
            if c == ";":
                if line.startswith(";TYPE:"):
                    kind_now = kind_no(line[6:].strip())
                elif line.startswith(";WIDTH:"):
                    try:
                        width_now = float(line[7:])
                    except ValueError:
                        pass
                elif line.startswith(";LAYER_CHANGE"):
                    if layer is not None:
                        finish(layer)
                    number += 1
                    layer = _Layer(number, 0.0, 0.0)
                    pending_z = None
                elif line.startswith(";Z:") and layer is not None and pending_z is None:
                    try:
                        layer.z = pending_z = float(line[3:])
                    except ValueError:
                        pass
                elif line.startswith(";HEIGHT:") and layer is not None and not layer.seg:
                    try:
                        layer.height = float(line[8:])
                    except ValueError:
                        pass
                elif line.startswith("; EXECUTABLE_BLOCK_END"):
                    break
                continue
            if c != "G" and c != "M":
                continue
            head = line[:3]
            if head in ("G1 ", "G0 ", "G2 ", "G3 "):
                nx, ny, e = x, y, 0.0
                has_e = False
                for tok in line.split(";", 1)[0].split()[1:]:
                    t = tok[0]
                    if t == "X":
                        nx = float(tok[1:]) if absolute_xy else x + float(tok[1:])
                    elif t == "Y":
                        ny = float(tok[1:]) if absolute_xy else y + float(tok[1:])
                    elif t == "E":
                        e, has_e = float(tok[1:]), True
                if has_e:
                    if relative_e is not True:
                        raise SlicerError(f"{path.name}: подача абсолютная (M82) — тело "
                                          f"G-code так не разобрать")
                    stat = report.by_kind[kinds[kind_now]]
                    stat["filament_mm"] += e
                    if e > 0 and (nx != x or ny != y):
                        stat["length_mm"] += math.hypot(nx - x, ny - y)
                        if head in ("G2 ", "G3 "):
                            report.arcs += 1
                        if layer is not None:
                            layer.seg.append((x, y, nx, ny))
                            layer.width.append(width_now)
                            layer.kind.append(kind_now)
                x, y = nx, ny
            elif head == "M83":
                relative_e = True
            elif head == "M82":
                relative_e = False
            elif line.startswith("G90"):
                absolute_xy = True
            elif line.startswith("G91"):
                absolute_xy = False
    if layer is not None:
        finish(layer)

    report.layers = number
    report.height_mm = max((z1 for _, z1 in z_of.values()), default=0.0)
    for stat in report.by_kind.values():
        stat["grams"] = stat["filament_mm"] * gram_per_mm
    report.support_g = sum(v["grams"] for k, v in report.by_kind.items() if k in SUPPORT_KINDS)
    report.part_g = sum(v["grams"] for k, v in report.by_kind.items() if k not in NOT_PART)
    report.overhang_mm = sum(overhang_by_layer.values())
    report.overhang_runs = _runs(overhang_by_layer, z_of)
    report.bridge_mm = sum(bridge_by_layer.values())
    report.bridge_runs = _runs(bridge_by_layer, z_of)
    return report
