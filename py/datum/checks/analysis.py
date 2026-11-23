"""Аналитические проверки: числа, а не геометрия.

Откуда взялись. В рабочем процессе CAD такие расчёты жили отдельными скриптами
(`calc.py`, `calc4.py`), печатали строки и заканчивались одним `assert ok`.
Узнать из них, НАСКОЛЬКО близко к краю, было нельзя: только «прошло» или «нет».
А близость к краю здесь и есть главное — флексура с деформацией 1.49 % при
пределе 1.5 % формально проходит, но сломается на сотом нажатии.

ГЛАВНОЕ ПРАВИЛО ЭТОГО МОДУЛЯ: НИ ОДНОГО СВОЙСТВА МАТЕРИАЛА ВНУТРИ. Модуль
упругости печатного PLA — это не константа, а диапазон от 2000 до 3600 МПа,
зависящий от ориентации слоёв, заполнения и температуры стола. Спрятав его в
таблицу, мы получили бы число, выглядящее точным, и уверенность на пустом
месте. Поэтому все свойства приходят аргументами: в проекте они объявляются
параметрами с происхождением `assumed` и диапазоном, а `sweep_assumed`
показывает, решает ли дело именно эта догадка.

Формулы записаны в тексте проверок целиком: их легко переврать, а ошибку в
коэффициенте по результату не заметишь.
"""

from __future__ import annotations

import math
import time

import numpy as np

from datum.checks.result import CheckResult
from datum.geom.solid import Solid

__all__ = ["beam_strain", "flexure_force", "euler_buckling", "cam_force",
           "mass_estimate", "com_support_margin", "tipping_margin"]

#: Как закреплён изгибаемый лист.
#:
#: «cantilever» — консоль: один конец заделан, второй свободен и поворачивается.
#: «guided» — направляемая балка: оба конца сохраняют угол, лист изгибается
#: буквой S. Так работает параллельная флексура, и деформация в ней ВДВОЕ
#: больше, чем в консоли при том же прогибе. Перепутать — значит ошибиться
#: ровно вдвое и не заметить.
BEAM_MODES = ("cantilever", "guided")


def _mk(id_: str, title: str, status: str, message: str, **kw) -> CheckResult:
    return CheckResult(id=id_, title=title, group="analysis", status=status,
                       message=message, **kw)


def _mode_factor(mode: str) -> float:
    """Множитель в ε = k · t · δ / L²."""
    if mode == "guided":
        return 3.0
    if mode == "cantilever":
        return 1.5
    raise ValueError(f"закрепление должно быть одним из {BEAM_MODES}, получено {mode!r}")


# --------------------------------------------------------------------- изгиб
def beam_strain(*, length: float, thickness: float, deflection: float,
                limit: float, mode: str = "guided", name: str = "флексура",
                margin_frac: float = 0.0) -> CheckResult:
    """Деформация крайнего волокна изгибаемого листа против предела материала.

    Формулы (вывод — школьный, но переврать легко, поэтому записан):

        направляемая балка   ε = 3·t·δ / L²
        консоль              ε = 1.5·t·δ / L²

    Для направляемой: прогиб δ = P·L³/(12·E·I), момент на заделке M = P·L/2,
    напряжение σ = M·(t/2)/I = 3·E·t·δ/L², деформация ε = σ/E.

    `limit` — предельная деформация материала (для печатного PLA около 0.015,
    для PETG около 0.025). Это не константа: она зависит от ориентации слоёв.
    Объявляйте её параметром с происхождением, а не числом в описании проверки.
    """
    t0 = time.perf_counter()
    if length <= 0 or thickness <= 0:
        raise ValueError("длина и толщина листа должны быть положительными")

    factor = _mode_factor(mode)
    strain = factor * thickness * abs(deflection) / (length ** 2)
    threshold = limit * (1.0 - margin_frac)
    ok = strain <= threshold

    how = "направляемая балка" if mode == "guided" else "консоль"
    msg = (f"{name}: {how} {length:g}×{thickness:g} мм при прогибе {deflection:g} мм — "
           f"деформация {strain * 100:.2f} % при пределе {limit * 100:.2f} %")
    if not ok:
        msg += f"; ПРЕВЫШЕН в {strain / limit:.2f} раза"
    elif strain > limit * 0.85:
        msg += "; запас меньше 15 %"

    return _mk(f"{name}.деформация", "Деформация листа при изгибе",
               "pass" if ok else "fail", msg,
               value=strain, unit="", op="<=", threshold=threshold,
               duration_ms=(time.perf_counter() - t0) * 1000)


def flexure_force(*, leaves: int, length: float, width: float, thickness: float,
                  modulus: float, deflection: float,
                  min_force: float = 0.0, max_force: float = math.inf,
                  mode: str = "guided", name: str = "флексура") -> CheckResult:
    """Сила пружины из листов на заданном прогибе.

        I = b·t³/12
        направляемая балка   k = n · 12·E·I / L³ = n · E·b·t³ / L³
        консоль              k = n ·  3·E·I / L³ = n · E·b·t³ / (4·L³)

    `modulus` в МПа, размеры в мм, сила выходит в ньютонах: МПа = Н/мм².

    Проверяется вилка: снизу — хватает ли силы на возврат (пружина, которая не
    возвращает, бесполезна), сверху — не слишком ли тяжело нажимать.
    """
    t0 = time.perf_counter()
    if min(leaves, length, width, thickness, modulus) <= 0:
        raise ValueError("число листов, размеры и модуль должны быть положительными")

    second_moment = width * thickness ** 3 / 12.0
    coefficient = 12.0 if mode == "guided" else 3.0
    if mode not in BEAM_MODES:
        raise ValueError(f"закрепление должно быть одним из {BEAM_MODES}, получено {mode!r}")
    stiffness = leaves * coefficient * modulus * second_moment / (length ** 3)
    force = stiffness * abs(deflection)

    ok = min_force <= force <= max_force
    msg = (f"{name}: {leaves} л. {length:g}×{thickness:g}×{width:g} мм, "
           f"жёсткость {stiffness:.3f} Н/мм, на прогибе {deflection:g} мм — "
           f"{force:.2f} Н")
    if force < min_force:
        msg += f"; МАЛО, нужно ≥ {min_force:g} Н"
    elif force > max_force:
        msg += f"; МНОГО, нужно ≤ {max_force:g} Н"

    # Запас до ближней границы вилки считает сам CheckResult: это общее
    # правило, а не особенность этой проверки.
    return _mk(f"{name}.сила", "Сила флексуры на прогибе",
               "pass" if ok else "fail", msg,
               value=force, unit="Н", op="in",
               threshold=(min_force, None if max_force == math.inf else max_force),
               duration_ms=(time.perf_counter() - t0) * 1000)


def euler_buckling(*, length: float, width: float, thickness: float,
                   modulus: float, load: float, ends: str = "pinned",
                   safety: float = 3.0, name: str = "стержень") -> CheckResult:
    """Потеря устойчивости сжатого стержня.

        P_кр = π²·E·I / (K·L)²,   I = b·t³/12 по слабой оси

    K — коэффициент приведения длины: шарниры 1.0, заделки 0.5, заделка-свобода
    2.0. Берётся СЛАБАЯ ось: стержень теряет устойчивость там, где тоньше, и
    считать по сильной — обычный способ получить бодрое число вместо честного.
    """
    t0 = time.perf_counter()
    factors = {"pinned": 1.0, "fixed": 0.5, "cantilever": 2.0}
    if ends not in factors:
        raise ValueError(f"закрепление должно быть одним из {tuple(factors)}, получено {ends!r}")
    if min(length, width, thickness, modulus) <= 0:
        raise ValueError("размеры и модуль должны быть положительными")

    weak = min(width, thickness)
    strong = max(width, thickness)
    second_moment = strong * weak ** 3 / 12.0
    effective = factors[ends] * length
    critical = math.pi ** 2 * modulus * second_moment / (effective ** 2)

    needed = load * safety
    ok = critical >= needed
    msg = (f"{name}: {width:g}×{thickness:g}×{length:g} мм, критическая сила "
           f"{critical:.1f} Н при нагрузке {load:g} Н "
           f"(запас {critical / load:.1f}× при требуемом {safety:g}×)")
    if not ok:
        msg += "; НЕДОСТАТОЧНО"

    return _mk(f"{name}.устойчивость", "Устойчивость сжатого стержня",
               "pass" if ok else "fail", msg,
               value=critical, unit="Н", op=">=", threshold=needed,
               duration_ms=(time.perf_counter() - t0) * 1000)


def cam_force(*, force: float, rise: float, run: float,
              friction_deg: float, min_out: float = 0.0,
              name: str = "кулачок") -> CheckResult:
    """Сила, прошедшая через кулачок или клин, с учётом трения.

        β = atan2(подъём, ход)
        F_вых = F_вх / tan(β + φ)

    φ — угол трения: для печатного пластика по пластику около 17° (µ≈0.3), и
    это ДОГАДКА, поэтому аргумент обязателен — объявляйте её параметром. Трение складывается с углом клина, а
    не вычитается: оно мешает и на подъёме, и на спуске, если толкать вверх по
    склону.

    Проверка отвечает на вопрос «хватит ли силы»: слишком крутой кулачок
    съедает её трением, и механизм встаёт. Так и вышло бы в дозаторе, где
    возврат шибера держится на преднатяге флексуры.
    """
    t0 = time.perf_counter()
    if run <= 0 or rise <= 0:
        raise ValueError("подъём и ход кулачка должны быть положительными")
    if force < 0:
        raise ValueError(f"сила на входе {force:g} Н: должна быть неотрицательной")
    if not 0 <= friction_deg < 90:
        raise ValueError(f"угол трения {friction_deg:g}°: ожидается от 0 до 90")

    angle = math.atan2(rise, run)
    total = angle + math.radians(friction_deg)
    # САМОТОРМОЖЕНИЕ. При β + φ ≥ 90° клин заперт: сколько ни дави, на выходе
    # ноль. Первая версия считала здесь `force / tan(β+φ)`, получала отрицательный
    # тангенс и подставляла БЕСКОНЕЧНОСТЬ — то есть объявляла запертый механизм
    # «хватит с запасом». Ровно ту беду, от которой проверка и страхует.
    locked = total >= math.pi / 2 - 1e-12
    out = 0.0 if locked else force / math.tan(total)

    ok = out >= min_out and not locked
    msg = (f"{name}: наклон {math.degrees(angle):.0f}° (подъём {rise:g} на ходе "
           f"{run:g}), трение {friction_deg:g}° — из {force:.2f} Н на выходе "
           f"{out:.2f} Н при нужных {min_out:g}")
    if locked:
        msg += (f"; САМОТОРМОЖЕНИЕ: наклон плюс угол трения "
                f"{math.degrees(total):.0f}° ≥ 90°, клин заперт и сила не проходит "
                f"вовсе. Положе кулачок — меньше {90 - friction_deg:.0f}°")
    elif not ok:
        msg += "; НЕ ХВАТИТ"
    elif min_out > 0 and out < min_out * 1.15:
        msg += "; запас меньше 15 %"

    return _mk(f"{name}.сила", "Сила через кулачок", "pass" if ok else "fail", msg,
               value=out, unit="Н", op=">=", threshold=min_out,
               duration_ms=(time.perf_counter() - t0) * 1000)


# ------------------------------------------------------------------- масса
def mass_estimate(part: Solid, *, density: float, fill: float = 1.0,
                  min_g: float = 0.0, max_g: float = math.inf,
                  name: str = "") -> CheckResult:
    """Масса детали по объёму и плотности.

        m = V · ρ · доля_заполнения

    `density` в г/см³ (PLA около 1.24, PETG около 1.27), объём берётся из
    геометрии в мм³. `fill` — доля настоящего материала: у детали с 20 %
    заполнения и четырьмя периметрами она заметно выше 0.2, поэтому это
    измеряемая величина, а не настройка слайсера. Объявляйте её параметром.
    """
    t0 = time.perf_counter()
    from datum.geom.measure import vol

    name = name or part.name or "деталь"
    volume_mm3 = float(vol(part))
    mass = volume_mm3 / 1000.0 * density * fill

    ok = min_g <= mass <= max_g
    msg = (f"{name}: объём {volume_mm3:.0f} мм³ при плотности {density:g} г/см³ "
           f"и заполнении {fill * 100:.0f} % — масса {mass:.1f} г")
    if not ok:
        msg += (f"; вне вилки {min_g:g}…"
                + ("∞" if max_g == math.inf else f"{max_g:g}") + " г")
    return _mk(f"{name}.масса", "Масса детали", "pass" if ok else "fail", msg,
               value=mass, unit="г", op="in",
               threshold=(min_g, None if max_g == math.inf else max_g),
               inputs=(name,), duration_ms=(time.perf_counter() - t0) * 1000)


# --------------------------------------------------------------- устойчивость
def _footprint(part: Solid, base_z: float, above: float = 0.2):
    """Опорный многоугольник: сечение чуть выше пола, выпуклая оболочка."""
    from shapely.geometry import MultiPoint

    from datum.geom.measure import section_xy

    poly = section_xy(part, base_z + above)
    if poly.is_empty:
        return None
    geoms = list(poly.geoms) if hasattr(poly, "geoms") else [poly]
    points = [p for g in geoms for p in g.exterior.coords]
    if len(points) < 3:
        return None
    return MultiPoint(points).convex_hull


def com_support_margin(part: Solid, *, base_z: float = 0.0,
                       min_margin: float = 2.0, name: str = "") -> CheckResult:
    """Центр масс внутри опоры и насколько далеко от края.

    Считается по СЕЧЕНИЮ у пола, а не по габариту: у детали на трёх ножках
    габарит вдвое больше настоящей опоры, и стоять она будет хуже, чем кажется.
    Берётся выпуклая оболочка сечения — именно на неё опирается тело.

    Центр масс — геометрический (тело однородно). У печатной детали с разным
    заполнением он смещается, но на порядок меньше, чем разница между
    габаритом и настоящей опорой.
    """
    t0 = time.perf_counter()
    from shapely.geometry import Point

    from datum.geom.measure import com

    name = name or part.name or "деталь"
    hull = _footprint(part, base_z)
    if hull is None:
        return _mk(f"{name}.опора", "Центр масс над опорой", "error",
                   f"{name}: у пола (Z={base_z:g}) нет сечения — деталь его не касается",
                   duration_ms=(time.perf_counter() - t0) * 1000)

    centre = com(part)
    point = Point(centre[0], centre[1])
    inside = hull.contains(point)
    distance = point.distance(hull.exterior)
    margin = distance if inside else -distance

    ok = margin >= min_margin
    where = "внутри опоры" if inside else "ЗА ПРЕДЕЛАМИ опоры"
    msg = (f"{name}: центр масс ({centre[0]:.1f}, {centre[1]:.1f}) {where}, "
           f"до края {margin:+.1f} мм при требуемых {min_margin:g}; "
           f"площадь опоры {hull.area:.0f} мм²")
    return _mk(f"{name}.опора", "Центр масс над опорой",
               "pass" if ok else "fail", msg,
               value=margin, unit="мм", op=">=", threshold=min_margin,
               inputs=(name,), duration_ms=(time.perf_counter() - t0) * 1000)


def tipping_margin(part: Solid, *, mass_g: float, push_n: float, push_z: float,
                   lateral_frac: float = 0.3, base_z: float = 0.0,
                   min_ratio: float = 1.5, name: str = "") -> CheckResult:
    """Опрокинется ли при нажатии.

    Нажимают вниз, но часть усилия всегда уходит вбок — рука не строго
    вертикальна. `lateral_frac` и есть эта доля (0.3 — разумная оценка для
    нажатия пальцем; это ДОГАДКА, объявляйте её параметром).

        момент опрокидывания   M_оп = F · доля · h
        удерживающий момент    M_уд = m · g · d

    где d — расстояние от проекции центра масс до ближайшего ребра опоры:
    именно через него тело и перевернётся. Считается по всем направлениям, и
    берётся худшее.
    """
    t0 = time.perf_counter()
    from shapely.geometry import Point

    from datum.geom.measure import com

    name = name or part.name or "деталь"
    hull = _footprint(part, base_z)
    if hull is None:
        return _mk(f"{name}.опрокидывание", "Устойчивость при нажатии", "error",
                   f"{name}: у пола (Z={base_z:g}) нет сечения — деталь его не касается",
                   duration_ms=(time.perf_counter() - t0) * 1000)

    centre = com(part)
    point = Point(centre[0], centre[1])
    lever = point.distance(hull.exterior)
    if not hull.contains(point):
        lever = -lever

    restoring = mass_g / 1000.0 * 9.81 * lever          # Н·мм: г→кг, мм остаются
    tipping = push_n * lateral_frac * abs(push_z - base_z)
    ratio = restoring / tipping if tipping > 0 else math.inf

    ok = ratio >= min_ratio
    msg = (f"{name}: удерживающий момент {restoring:.0f} Н·мм против "
           f"опрокидывающего {tipping:.0f} Н·мм — запас {ratio:.2f}× "
           f"при требуемом {min_ratio:g}× "
           f"(масса {mass_g:g} г, плечо {lever:.1f} мм, нажатие {push_n:g} Н "
           f"на высоте {push_z - base_z:.0f} мм, вбок {lateral_frac * 100:.0f} %)")
    if not ok:
        msg += "; ОПРОКИНЕТСЯ"

    return _mk(f"{name}.опрокидывание", "Устойчивость при нажатии",
               "pass" if ok else "fail", msg,
               value=ratio if math.isfinite(ratio) else None,
               unit="×", op=">=", threshold=min_ratio,
               inputs=(name,), duration_ms=(time.perf_counter() - t0) * 1000)
