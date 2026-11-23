"""Масса и инерция ПЕЧАТНОЙ детали — так, как её кладёт слайсер.

Симуляции нужна масса звена, а печатная деталь не сплошная: стенки, сплошные
верх и низ, внутри — заполнение. По объёму сетки масса выходит в полтора-два
раза больше настоящей (Gear Cube: скала 470 г сплошной против 256 г в G-code).

Модель повторяет слайсер по слоям, в позе печати:

  * кольцо стенок — `walls` линий от контура (наружная `w_outer`, внутренние
    `w_inner`), и толщина оболочки соблюдается ПО НОРМАЛИ к поверхности, а не
    только в плоскости слоя: точка слоя k, лежащая ближе sqrt(t² − (j·h)²) к
    БОКОВОЙ границе соседнего слоя k±j, — тоже оболочка. Так слайсер делает с
    `ensure_vertical_shell_thickness`; без этого конические шестерни выходили
    на 6…9 % легче (замер ниже);
  * сплошной верх и низ — где над точкой (под ней) нет материала в пределах
    `top_layers` (`bottom_layers`) слоёв. Пустота над точкой — забота ЭТОГО
    правила, а не оболочки: иначе всё под горизонтальной гранью становилось
    сплошным (дно Gear Cube +20 %);
  * остальное — заполнение долей `infill`;
  * всё — умножено на подачу (`filament_flow_ratio × print_flow_ratio` профиля):
    слайсер кладёт меньше геометрического объёма.

Сверено с G-code ElegooSlicer всех 17 деталей Gear Cube — пластик самой детали
(чистая подача по типам линий, без прочистки и каймы), их же настройки:
средняя ошибка и разброс — в `tests/integration/test_fab_mass.py`.

Инерция — из тех же слоёв: у каждого куска слоя (сплошное с весом 1,
заполнение с весом `infill`) площадь, центр и моменты сечения точно по формулам
Грина (`section_moments`), слой — тонкая пластина толщины h. Заполнение
считается размазанным равномерно: настоящая решётка (гироид) на масштабе звена
неотличима от сплошной среды той же плотности.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from datum.fab.settings import PrintSettings

__all__ = ["MassProps", "print_mass", "cached_print_mass", "uniform_props", "upright_matrix",
           "GRID_MM", "MAX_LAYERS", "MODEL_VERSION", "model_digest"]

#: Версия модели массы — в ключе кеша: правка правил не должна отдавать старые числа.
MODEL_VERSION = 1

#: Сетка точности для операций с многоугольниками, мм. GEOS на касающихся
#: контурах соседних слоёв роняет наложение («Unable to determine overlay result
#: geometry dimension», замерено на Gear Cube); на сетке 1e-4 — устойчиво, а
#: площадь меняется на доли ppm.
GRID_MM = 1e-4

#: Упрощение контура сечения, мм: на массу не влияет (меньше ширины линии в 20
#: раз), а скульптуре в 270 тыс. треугольников экономит половину времени.
SIMPLIFY_MM = 0.02

#: Больше слоёв не режем: высокую деталь с мелким слоем считаем на этом числе
#: равных слоёв (толщина слоя растёт, вертикальные правила — в мм, не в слоях).
MAX_LAYERS = 1500


@dataclass(frozen=True)
class MassProps:
    """Масса и инерция детали в системе координат её скрипта (как построена)."""

    mass_g: float
    com_mm: tuple[float, float, float]
    #: Тензор инерции относительно центра масс, г·мм² (3×3, симметричный).
    inertia_g_mm2: tuple[tuple[float, float, float], ...]
    solid_volume_mm3: float          # объём сетки
    shell_fraction: float            # доля объёма, лёгшая сплошным (стенки, верх, низ)
    layers: int
    settings: PrintSettings

    def as_dict(self) -> dict:
        return {"mass_g": round(self.mass_g, 4), "com_mm": [round(v, 4) for v in self.com_mm],
                "inertia_g_mm2": [[round(v, 3) for v in row] for row in self.inertia_g_mm2],
                "solid_volume_mm3": round(self.solid_volume_mm3, 3),
                "shell_fraction": round(self.shell_fraction, 4), "layers": self.layers,
                "settings": self.settings.as_dict()}


def upright_matrix(up: str) -> np.ndarray:
    """Матрица поворота `xform.upright`: p_печать = R · p_как_построена.

    Повороты те же, что в `xform.UPRIGHT` (rx, ry в градусах, правая рука);
    совпадение держит тест — по вершинам настоящего `upright`.
    """
    from datum.geom.xform import UPRIGHT

    key = str(up or "+Z").strip().upper()
    if key not in UPRIGHT:
        raise ValueError(f"ось вверх задаётся как +Z, -Z, +X, -X, +Y или -Y, получено {up!r}")
    r = np.eye(3)
    turn = UPRIGHT[key]
    if "rx" in turn:
        a = math.radians(turn["rx"])
        r = np.array([[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]]) @ r
    if "ry" in turn:
        a = math.radians(turn["ry"])
        r = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]]) @ r
    return r


def _polys(g):
    """Только площадная часть результата наложения: GEOS оставляет рядом с ней
    отрезки и точки касания, и следующая операция падала на «mixed-dimension»."""
    from shapely.geometry import MultiPolygon, Polygon

    if g.is_empty:
        return MultiPolygon()
    if isinstance(g, (Polygon, MultiPolygon)):
        return g
    parts = [p for p in getattr(g, "geoms", []) if isinstance(p, (Polygon, MultiPolygon))]
    flat = []
    for p in parts:
        flat.extend(p.geoms if isinstance(p, MultiPolygon) else [p])
    return MultiPolygon(flat) if flat else MultiPolygon()


def _inter(a, b):
    import shapely

    if a.is_empty or b.is_empty:
        return _polys(a.difference(a)) if not a.is_empty else a
    return _polys(shapely.intersection(a, b, grid_size=GRID_MM))


def _diff(a, b):
    import shapely

    if a.is_empty or b.is_empty:
        return a
    return _polys(shapely.difference(a, b, grid_size=GRID_MM))


def _shrink(a, r: float):
    import shapely

    if r <= 0 or a.is_empty:
        return a
    return _polys(shapely.set_precision(a.buffer(-r, join_style=2), GRID_MM))


def _moments(geom) -> tuple[float, float, float, float, float, float]:
    """(A, ∫x, ∫y, ∫x², ∫y², ∫xy) куска слоя — сырые, относительно начала."""
    from datum.geom.measure import section_moments

    if geom.is_empty or geom.area <= 0:
        return (0.0,) * 6
    a, cu, cv, iuu, ivv, iuv = section_moments(geom)
    return a, a * cu, a * cv, iuu + a * cu * cu, ivv + a * cv * cv, iuv + a * cu * cv


def print_mass(solid, settings: PrintSettings, *, up: str = "+Z") -> MassProps:
    """Масса, центр масс и инерция детали, напечатанной с `settings` в позе `up`.

    Режется деталь в позе печати; ответ — в системе координат скрипта детали.
    """
    import shapely

    from datum.geom.measure import bbox, section_xy, vol
    from datum.geom.xform import upright

    s = settings
    placed = upright(solid, up) if up else solid
    lo, hi = bbox(placed)
    height = float(hi[2] - lo[2])
    if height <= 0:
        raise ValueError("у детали нет высоты — массу по слоям не посчитать")
    n = max(1, min(MAX_LAYERS, int(round(height / s.layer))))
    dz = height / n
    zs = lo[2] + (np.arange(n) + 0.5) * dz
    layers = [shapely.set_precision(section_xy(placed, float(z)).simplify(SIMPLIFY_MM), GRID_MM)
              for z in zs]
    layers = [_polys(g) for g in layers]

    t = s.wall_thickness
    reach = int(t // dz)                       # соседние слои, где действует оболочка по нормали
    top_n = s.top_count
    bot_n = s.bottom_count
    bands = [_diff(g, _shrink(g, t)) for g in layers]   # кольцо стенок каждого слоя

    raw = np.zeros(10)                         # m, mx, my, mz, mxx, myy, mzz, mxy, mxz, myz
    shell_vol = total_vol = 0.0
    for k, a in enumerate(layers):
        if a.is_empty:
            continue
        core = _shrink(a, t)
        for j in range(1, reach + 1):
            r = math.sqrt(max(t * t - (j * dz) ** 2, 0.0))
            for kk in (k - j, k + j):
                if 0 <= kk < n and r > 0 and not layers[kk].is_empty:
                    ring = bands[kk] if r >= t - 1e-12 else _diff(layers[kk], _shrink(layers[kk], r))
                    core = _diff(core, ring)
        above = layers[k + 1:k + 1 + top_n]
        below = layers[max(0, k - bot_n):k]
        if len(above) < top_n or len(below) < bot_n:
            core = _diff(core, core)           # у верха и низа детали — сплошной слой
        else:
            for b in (*above, *below):
                core = _inter(core, b)
        solid_part = _diff(a, core)
        z = float(zs[k])
        for geom, w in ((solid_part, 1.0), (core, s.infill)):
            if w <= 0:
                continue
            ar, sx, sy, sxx, syy, sxy = _moments(geom)
            if ar <= 0:
                continue
            k_ = w * dz
            raw += k_ * np.array([ar, sx, sy, ar * z, sxx, syy, ar * (z * z + dz * dz / 12),
                                  sxy, sx * z, sy * z])
        shell_vol += solid_part.area * dz
        total_vol += a.area * dz

    rho = s.density_g_cm3 * s.flow / 1000.0     # г/мм³ положенного пластика
    m = raw[0] * rho
    if m <= 0:
        raise ValueError("масса вышла нулевой — у детали нет сечений по слоям")
    c = raw[1:4] / raw[0]
    xx, yy, zz = raw[4] / raw[0], raw[5] / raw[0], raw[6] / raw[0]
    xy, xz, yz = raw[7] / raw[0], raw[8] / raw[0], raw[9] / raw[0]
    # второй центральный момент: ∫(r−c)(r−c)ᵀ dm
    cov = m * np.array([[xx - c[0] ** 2, xy - c[0] * c[1], xz - c[0] * c[2]],
                        [xy - c[0] * c[1], yy - c[1] ** 2, yz - c[1] * c[2]],
                        [xz - c[0] * c[2], yz - c[1] * c[2], zz - c[2] ** 2]])
    inertia = np.trace(cov) * np.eye(3) - cov          # I = tr(Σ)·E − Σ
    # из позы печати — обратно в систему скрипта: p_печать = R·p, значит p = Rᵀ·p_печать
    rm = upright_matrix(up) if up else np.eye(3)
    com = rm.T @ c
    inertia = rm.T @ inertia @ rm
    return MassProps(mass_g=float(m), com_mm=tuple(float(v) for v in com),
                     inertia_g_mm2=tuple(tuple(float(v) for v in row) for row in inertia),
                     solid_volume_mm3=float(vol(placed)),
                     shell_fraction=float(shell_vol / total_vol) if total_vol else 0.0,
                     layers=n, settings=s)


def uniform_props(solid, mass_g: float) -> tuple[float, np.ndarray, np.ndarray]:
    """Покупное тело известной массы: (масса г, центр мм, инерция г·мм² о центре).

    Плотность считается равномерной — у мотора и платы это не так, но
    распределения внутри них не знает никто, а масса и центр по форме — честная
    оценка. Инерция — по сетке (`trimesh`, точно для замкнутой сетки)."""
    from datum.geom.convert import to_trimesh
    from datum.geom.measure import man

    mesh = to_trimesh(man(solid))
    if mesh.volume <= 0:
        raise ValueError("у тела нет объёма — инерцию покупного не посчитать")
    mesh.density = mass_g / mesh.volume                    # г/мм³
    return float(mass_g), np.asarray(mesh.center_mass, float), np.asarray(mesh.moment_inertia, float)


_MODEL_DIGEST: str | None = None


def model_digest() -> str:
    """Отпечаток кода модели массы (`datum/fab/`). Раз на процесс.

    Прежде ключ кеша нёс только ручной `MODEL_VERSION`: правка модели без
    правки числа оставляла в `.datum/mass` массы, посчитанные прежним кодом,
    — а по ним идут симуляция и ведомость (аудит 2026-11-21, §6.3.9)."""
    global _MODEL_DIGEST
    if _MODEL_DIGEST is None:
        import hashlib
        from pathlib import Path

        h = hashlib.sha256()
        root = Path(__file__).resolve().parent
        for f in sorted(root.glob("*.py")):
            h.update(f.name.encode("utf-8") + b"\0" + f.read_bytes() + b"\0")
        _MODEL_DIGEST = h.hexdigest()[:16]
    return _MODEL_DIGEST


def cached_print_mass(solid, settings: PrintSettings, *, up: str, cache_dir) -> MassProps:
    """`print_mass` с кешем на диске: ключ — ФОРМА (`Solid.digest`), настройки и поза.

    Скала Gear Cube (270 тыс. треугольников) считается 30 с; симуляция зовёт
    массу на каждый прогон, а развёртка — на каждое значение параметра."""
    import hashlib
    import json
    from pathlib import Path

    key = hashlib.sha256(json.dumps({"shape": solid.digest(), "up": up or "",
                                     "settings": {k: v for k, v in settings.as_dict().items()
                                                  if k != "sources"},
                                     "model": MODEL_VERSION, "code": model_digest()},
                                    sort_keys=True).encode()).hexdigest()
    path = Path(cache_dir) / f"{key}.json"
    if path.is_file():
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            return MassProps(mass_g=d["mass_g"], com_mm=tuple(d["com_mm"]),
                             inertia_g_mm2=tuple(tuple(r) for r in d["inertia_g_mm2"]),
                             solid_volume_mm3=d["solid_volume_mm3"],
                             shell_fraction=d["shell_fraction"], layers=d["layers"],
                             settings=settings)
        except (OSError, ValueError, KeyError):
            pass                                            # битая запись — посчитаем заново
    props = print_mass(solid, settings, up=up)
    _write_atomically(path, json.dumps({
        "mass_g": props.mass_g, "com_mm": list(props.com_mm),
        "inertia_g_mm2": [list(r) for r in props.inertia_g_mm2],
        "solid_volume_mm3": props.solid_volume_mm3, "shell_fraction": props.shell_fraction,
        "layers": props.layers}))
    return props


def _write_atomically(path, text: str) -> None:
    """Свой временный файл у каждого писателя и переименование: две симуляции
    одной детали из разных процессов не портят запись друг другу."""
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
