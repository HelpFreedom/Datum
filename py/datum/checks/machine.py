"""Детали машин: ось в вилке, ходовой винт, балка, подшипник, лишние связи.

Эти проверки пришли из методики конструктора, а не из провалов корпуса, — но
класс ошибки у них тот же, что у «держит по касанию»: проверили ОДИН вид
отказа из нескольких. Разобранный пример, с которого всё началось: ось ⌀10 в
вилке под 1000 кгс по срезу даёт 62 МПа и «проходит», а изгиб той же оси —
325…375 МПа при пределе текучести 220. Сопромат не врал — неполным был
расчёт. Поэтому ось здесь проверяется сразу на срез, смятие и изгиб, и каждый
вид — против СВОЕГО допускаемого: среднее касательное — с [τ], смятие — с
[σсм], изгиб — с [σи]. Мизес против [τ] — ошибка методики.

Правило модуля `analysis` действует и тут: СВОЙСТВ МАТЕРИАЛА ВНУТРИ НЕТ.
Допускаемые напряжения, модуль, коэффициенты трения, C и C0 подшипника
приходят аргументами — в проекте это параметры с происхождением (`standard`
со ссылкой на справочник или каталог, `assumed` с диапазоном), и развёртка по
незамеренному показывает, решает ли дело именно эта догадка.

Формулы — из «Деталей машин» (Иванов), каталога SKF и сопромата; записаны
целиком там, где считаются.
"""

from __future__ import annotations

import math
import time

from datum.checks.result import CheckResult
from datum.geom.solid import Solid

__all__ = ["pin_joint", "screw_drive", "beam_deflection", "bearing_static",
           "redundant_constraints", "JOINT_CONSTRAINTS", "SCREW_PROFILES"]


def _mk(id_: str, title: str, status: str, message: str, **kw) -> CheckResult:
    return CheckResult(id=id_, title=title, group="machine", status=status,
                       message=message, **kw)


def _positive(**values) -> None:
    for key, v in values.items():
        if not (isinstance(v, (int, float)) and not isinstance(v, bool)
                and math.isfinite(v) and v > 0):
            raise ValueError(f"{key} должно быть положительным числом, получено {v!r}")


def _whole(value, key: str, lowest: int, highest: int | None = None) -> int:
    """Целое — в том числе 4.0: параметры проекта приходят числами с плавающей
    точкой, и `${params.STARTS}` иначе отвергался бы как «не целое»."""
    ok = (isinstance(value, (int, float)) and not isinstance(value, bool)
          and math.isfinite(value) and float(value).is_integer())
    if not ok or value < lowest or (highest is not None and value > highest):
        span = f"от {lowest}" + (f" до {highest}" if highest is not None else "")
        raise ValueError(f"{key} — целое {span}, получено {value!r}")
    return int(value)


def _verdict(ok: bool) -> str:
    return "в допуске" if ok else "БОЛЬШЕ ДОПУСТИМОГО"


# ------------------------------------------------------------- ось в вилке
def pin_joint(*, force: float, d: float, lug: float, fork: float,
              shear_allow: float, bearing_allow: float, bending_allow: float,
              gap: float = 0.0, fit: str = "clearance",
              fork_bearing_allow: float | None = None,
              name: str = "ось") -> list[CheckResult]:
    """Ось (палец) в проушине и вилке: срез, смятие и изгиб — все три.

    Проушина толщиной `lug` между двумя щеками вилки толщиной `fork` каждая,
    зазор `gap` между проушиной и щекой с каждой стороны. Два среза.

        срез     τ = F / (2·π·d²/4)            против [τ]  (τmax ≈ 4/3·τ)
        смятие   σ = F / (d·lug) в проушине,
                     F / (2·d·fork) в щеках     против [σсм]
        изгиб    M = F·(2·fork + lug + 4·gap)/8  ось в щеках с зазором,
                 M = F·(lug + 4·gap)/8           ось в щеках плотно;
                 σ = M / W,  W = π·d³/32        против [σи]

    Изгиб часто в разы опаснее среза — именно его и забывают.
    """
    t0 = time.perf_counter()
    _positive(force=force, d=d, lug=lug, fork=fork, shear_allow=shear_allow,
              bearing_allow=bearing_allow, bending_allow=bending_allow)
    if gap < 0:
        raise ValueError(f"зазор {gap:g} мм не бывает отрицательным")
    if fit not in ("clearance", "tight"):
        raise ValueError(f"посадка оси в щеках — 'clearance' или 'tight', получено {fit!r}")
    fork_allow = bearing_allow if fork_bearing_allow is None else fork_bearing_allow
    _positive(fork_bearing_allow=fork_allow)

    area = math.pi * d * d / 4
    tau = force / (2 * area)
    lug_bearing = force / (d * lug)
    fork_bearing = force / (2 * d * fork)
    arm = (2 * fork + lug + 4 * gap) if fit == "clearance" else (lug + 4 * gap)
    moment = force * arm / 8
    w = math.pi * d ** 3 / 32
    sigma = moment / w
    ms = (time.perf_counter() - t0) * 1000

    # Смятие — худшее из проушины и щёк, каждое против своего допускаемого:
    # проушина печатная, щёки металлические — обычное дело.
    use_lug = lug_bearing / bearing_allow >= fork_bearing / fork_allow
    worst_bearing, worst_allow, where = ((lug_bearing, bearing_allow, "проушине") if use_lug
                                         else (fork_bearing, fork_allow, "щеках"))
    geometry = f"⌀{d:g}, проушина {lug:g}, щёки 2×{fork:g}, зазор {gap:g} мм"
    return [
        _mk(f"{name}.срез", "Ось: срез", "pass" if tau <= shear_allow else "fail",
            f"{name}: срез τ = F/(2·πd²/4) = {force:.0f}/{2 * area:.1f} = {tau:.1f} МПа "
            f"(τmax ≈ {4 / 3 * tau:.1f}) против [τ] {shear_allow:g} — {_verdict(tau <= shear_allow)} "
            f"({geometry})",
            value=tau, unit="МПа", op="<=", threshold=shear_allow, duration_ms=ms),
        _mk(f"{name}.смятие", "Ось: смятие",
            "pass" if worst_bearing <= worst_allow else "fail",
            f"{name}: смятие в проушине F/(d·{lug:g}) = {lug_bearing:.1f} МПа, в щеках "
            f"F/(2·d·{fork:g}) = {fork_bearing:.1f} МПа; хуже в {where}: "
            f"{worst_bearing:.1f} против [σсм] {worst_allow:g} — "
            f"{_verdict(worst_bearing <= worst_allow)}",
            value=worst_bearing, unit="МПа", op="<=", threshold=worst_allow, duration_ms=ms),
        _mk(f"{name}.изгиб", "Ось: изгиб", "pass" if sigma <= bending_allow else "fail",
            f"{name}: изгиб M = F·{arm:g}/8 = {moment / 1000:.1f} Н·м "
            f"({'зазор в щеках' if fit == 'clearance' else 'плотно в щеках'}), "
            f"σ = M/(πd³/32) = {sigma:.0f} МПа против [σи] {bending_allow:g} — "
            f"{_verdict(sigma <= bending_allow)}"
            + ("" if sigma <= bending_allow else
               ". Изгиб растёт с плечом: тоньше щёки и проушина относительно d, "
                   "меньше зазор, ось плотно в щеках; W растёт как d³"),
            value=sigma, unit="МПа", op="<=", threshold=bending_allow, duration_ms=ms),
    ]


# ------------------------------------------------------------- ходовой винт
#: Угол профиля резьбы, градусы, и средний диаметр по номинальному и шагу.
SCREW_PROFILES = {
    "metric": (60.0, lambda d, p: d - 0.6495 * p),
    "trapezoid": (30.0, lambda d, p: d - 0.5 * p),
    "square": (0.0, lambda d, p: d - 0.5 * p),
}


def screw_drive(*, d: float, pitch: float, force: float, friction: float,
                starts: int = 1, profile: str = "metric", d2: float | None = None,
                collar_friction: float = 0.0, collar_d: float = 0.0,
                max_torque: float | None = None, safety: float = 1.5,
                self_locking: bool | None = None,
                name: str = "винт") -> list[CheckResult]:
    """Винтовая пара: КПД, самоторможение, момент на осевую силу.

        ход Ph = шаг·заходы,  ψ = atan(Ph / (π·d₂))
        φ' = atan(f / cos(α/2))                    приведённый угол трения
        η  = tg ψ / tg(ψ + φ')
        T↑ = F·d₂/2·tg(ψ + φ') + F·f_оп·D_оп/2     поднять (толкать)
        T↓ = F·d₂/2·tg(φ' − ψ) + F·f_оп·D_оп/2     опустить; < 0 — груз сам крутит винт
        самоторможение при ψ ≤ φ'

    `max_torque` — сколько есть (рука, мотор), Н·мм; требуется T↑·safety.
    `self_locking` — нужно ли самоторможение (True), или наоборот винт
    должен проворачиваться от осевой силы (False); None — не проверять.
    Вибрации ослабляют и самотормозящую пару.
    """
    t0 = time.perf_counter()
    _positive(d=d, pitch=pitch, force=force, safety=safety)
    starts = _whole(starts, "число заходов", 1)
    if not (0 < friction < 1):
        raise ValueError(f"коэффициент трения {friction!r}: ожидается от 0 до 1")
    if profile not in SCREW_PROFILES:
        raise ValueError(f"профиль — один из {tuple(SCREW_PROFILES)}, получено {profile!r}")
    alpha, mean = SCREW_PROFILES[profile]
    d2 = mean(d, pitch) if d2 is None else d2
    _positive(d2=d2)
    lead = pitch * starts
    psi = math.atan(lead / (math.pi * d2))
    phi = math.atan(friction / math.cos(math.radians(alpha / 2)))
    if psi + phi >= math.pi / 2 - 1e-9:
        # tg(ψ+φ') здесь бесконечен или отрицателен: формула момента подъёма
        # перестаёт быть моментом. Прежде выходило «нужно −1.78 Н·м, есть
        # 0.01 — хватает» и зелёная проверка (аудит 2026-11-21; тот же случай,
        # что самоторможение кулачка при β+φ ≥ 90°).
        ms = (time.perf_counter() - t0) * 1000
        return [_mk(
            f"{name}.момент", "Винт: хватит ли момента", "fail",
            f"{name}: {profile} ⌀{d:g}×{pitch:g}{f'×{starts} зах.' if starts > 1 else ''}, "
            f"ход {pitch * starts:g} мм — ЗАКЛИНИВАНИЕ ПРИ ПОДЪЁМЕ: ψ = "
            f"{math.degrees(psi):.2f}° и φ' = {math.degrees(phi):.2f}° (f = {friction:g}) "
            f"в сумме ≥ 90°, вращением такой винт груз не поднимает ни при каком моменте. "
            f"Уменьшите ход (меньше заходов или шаг), увеличьте диаметр или снизьте трение",
            value=math.degrees(psi + phi), unit="°", op="<", threshold=90.0,
            duration_ms=ms)]
    eta = math.tan(psi) / math.tan(psi + phi)
    collar = force * collar_friction * collar_d / 2
    raise_t = force * d2 / 2 * math.tan(psi + phi) + collar
    lower_t = force * d2 / 2 * math.tan(phi - psi) + collar
    locks = psi <= phi
    ms = (time.perf_counter() - t0) * 1000

    head = (f"{name}: {profile} ⌀{d:g}×{pitch:g}{f'×{starts} зах.' if starts > 1 else ''}, "
            f"d₂ {d2:.3f}, ход {lead:g} мм; ψ = {math.degrees(psi):.2f}°, "
            f"φ' = {math.degrees(phi):.2f}° при f = {friction:g}")
    out = [_mk(f"{name}.кпд", "Винт: КПД", "pass",
               f"{head}; КПД η = tgψ/tg(ψ+φ') = {eta * 100:.0f} %, "
               f"{'самотормозит' if locks else 'НЕ самотормозит'}; поднять — "
               f"{raise_t / 1000:.2f} Н·м, опустить — {lower_t / 1000:+.2f} Н·м"
               + (" (минус: груз сам крутит винт — держите тормозом или мотором)"
                  if lower_t < 0 else ""),
               value=eta, unit="доля", op="none", severity="info", duration_ms=ms)]
    if max_torque is not None:
        _positive(max_torque=max_torque)
        need = raise_t * safety
        ok = need <= max_torque
        out.append(_mk(
            f"{name}.момент", "Винт: хватит ли момента", "pass" if ok else "fail",
            f"{name}: на {force:.0f} Н нужно {raise_t / 1000:.2f} Н·м, с запасом ×{safety:g} — "
            f"{need / 1000:.2f} Н·м, есть {max_torque / 1000:.2f} — "
            + ("хватает" if ok else "НЕ ХВАТАЕТ")
            + ". У шагового мотора момент падает с оборотами — смотрите его кривую",
            value=need, unit="Н·мм", op="<=", threshold=max_torque, duration_ms=ms))
    if self_locking is not None:
        ok = locks == bool(self_locking)
        want = ("нужно самоторможение" if self_locking
                else "нужна обратимость (винт проворачивается от силы)")
        out.append(_mk(
            f"{name}.самоторможение", "Винт: самоторможение", "pass" if ok else "fail",
            f"{name}: {want}; ψ {math.degrees(psi):.2f}° "
            f"{'≤' if locks else '>'} φ' {math.degrees(phi):.2f}° — "
            f"{'самотормозит' if locks else 'не самотормозит'}"
            + ("" if ok else (". Меньше ход или больше трение" if self_locking
                              else ". Нужен больший ход (многозаходный винт) или меньше трение")),
            value=math.degrees(psi), unit="°", op="<=" if self_locking else ">=",
            threshold=math.degrees(phi), duration_ms=ms))
    return out


# ------------------------------------------------------------------ балка
#: Прогиб δ = k_δ·F·L³/(E·I) и наибольший момент M = k_M·F·L для типовых
#: схем. F при равномерной нагрузке — ВСЯ сила на пролёте (q·L).
BEAM_CASES = {
    ("pinned", "point"): (1 / 48, 1 / 4),
    ("fixed", "point"): (1 / 192, 1 / 8),
    ("cantilever", "point"): (1 / 3, 1.0),
    ("pinned", "uniform"): (5 / 384, 1 / 8),
    ("fixed", "uniform"): (1 / 384, 1 / 12),
    ("cantilever", "uniform"): (1 / 8, 1 / 2),
}

_SECTION_PLANE = {"x": ("yz", ("y", "z")), "y": ("xz", ("x", "z")), "z": ("xy", ("x", "y"))}


def _part_section(part: Solid, along: str, toward: str, at: float | None):
    """I и расстояние до крайнего волокна по НАСТОЯЩЕМУ сечению детали."""
    from datum.geom.measure import bbox, section_moments, section_xy, section_xz, section_yz

    along = along.strip().lower()
    axis = toward.strip().lower().lstrip("+-")
    if along not in _SECTION_PLANE or axis not in "xyz" or len(axis) != 1:
        raise ValueError(f"ось балки и направление силы — x, y или z; получено "
                         f"along={along!r}, toward={toward!r}")
    if axis == along:
        raise ValueError(f"сила вдоль оси балки ({toward}) — это растяжение или сжатие, не изгиб")
    plane, coords = _SECTION_PLANE[along]
    lo, hi = bbox(part)
    k = "xyz".index(along)
    at = (lo[k] + hi[k]) / 2 if at is None else at
    section = {"xy": section_xy, "xz": section_xz, "yz": section_yz}[plane](part, at)
    if section.is_empty:
        raise ValueError(f"сечение {plane.upper()} на {along}={at:g} пустое — балки там нет")
    area, cu, cv, iuu, ivv, iuv = section_moments(section)
    vu, vv = (1.0, 0.0) if coords[0] == axis else (0.0, 1.0)
    second = vu * vu * iuu + 2 * vu * vv * iuv + vv * vv * ivv
    pts = [c for g in getattr(section, "geoms", [section]) for c in g.exterior.coords]
    fiber = max(abs((u - cu) * vu + (v - cv) * vv) for u, v in pts)
    asym = abs(iuv) > 1e-6 * max(iuu, ivv)
    return second, fiber, area, f"сечение {plane.upper()} на {along}={at:g}", asym


def beam_deflection(part: Solid | None = None, *, length: float, load: float,
                    modulus: float, support: str = "pinned", load_kind: str = "point",
                    along: str = "x", toward: str = "-z", at: float | None = None,
                    second_moment: float | None = None, fiber: float | None = None,
                    max_deflection: float | None = None, allow_stress: float | None = None,
                    name: str = "балка") -> list[CheckResult]:
    """Прогиб и напряжение балки по типовым схемам.

    `support`: pinned — шарнир–шарнир, fixed — заделка–заделка, cantilever —
    консоль, between — жёсткость опор неизвестна, и ответ — вилка от заделки
    до шарнира: реальность между ними. `load_kind`: point — сила в середине
    пролёта (у консоли — на конце), uniform — равномерно, `load` — вся сила.

    Сечение — ИЗ ДЕТАЛИ (`part`, ось балки `along`, куда давит сила `toward`,
    место сечения `at`), а не из формулы профиля: деталь с рёбрами и
    облегчениями считается такой, какая она есть. Или явно — `second_moment`
    (мм⁴) и `fiber` (мм до крайнего волокна).

        δ = k_δ · F·L³ / (E·I),   M = k_M · F·L,   σ = M·y / I
    """
    t0 = time.perf_counter()
    _positive(length=length, load=load, modulus=modulus)
    if load_kind not in ("point", "uniform"):
        raise ValueError(f"нагрузка — 'point' или 'uniform', получено {load_kind!r}")
    cases = ("fixed", "pinned") if support == "between" else (support,)
    for c in cases:
        if (c, load_kind) not in BEAM_CASES:
            raise ValueError(f"опоры — pinned, fixed, cantilever или between; получено {support!r}")
    if max_deflection is None and allow_stress is None:
        raise ValueError("задайте max_deflection (мм) и/или allow_stress (МПа): без порога "
                         "проверке нечего сказать")
    note = ""
    if part is not None:
        if second_moment is not None or fiber is not None:
            raise ValueError("сечение — либо из детали (part), либо явно "
                             "(second_moment и fiber), не оба сразу")
        second_moment, fiber, area, where, asym = _part_section(part, along, toward, at)
        note = f"{where}: I = {second_moment:.0f} мм⁴, y = {fiber:.2f} мм, A = {area:.1f} мм²"
        if asym:
            note += ("; сечение НЕСИММЕТРИЧНО относительно оси силы — балка будет гнуться "
                     "и вбок, а считается только прогиб по силе")
    elif second_moment is None or fiber is None:
        raise ValueError("нужно сечение: деталь (part) или second_moment и fiber")
    else:
        _positive(second_moment=second_moment, fiber=fiber)
        note = f"I = {second_moment:g} мм⁴, y = {fiber:g} мм"

    rows = []
    for c in cases:
        kd, km = BEAM_CASES[(c, load_kind)]
        delta = kd * load * length ** 3 / (modulus * second_moment)
        moment = km * load * length
        rows.append((c, delta, moment, moment * fiber / second_moment))
    ms = (time.perf_counter() - t0) * 1000
    label = {"pinned": "шарнир", "fixed": "заделка", "cantilever": "консоль"}
    scheme = (f"{'сила в середине' if load_kind == 'point' and support != 'cantilever' else 'сила на конце' if load_kind == 'point' else 'равномерно'} "
              f"{load:.1f} Н, пролёт {length:g} мм, E {modulus:g} МПа")

    def judge(values, limit):
        lo_v, hi_v = min(values), max(values)
        if hi_v <= limit:
            return "pass", hi_v
        if lo_v > limit:
            return "fail", hi_v
        return "warn", hi_v                    # порог внутри вилки опор

    out = []
    for key, idx, limit, unit, title in (("прогиб", 1, max_deflection, "мм", "Балка: прогиб"),
                                         ("напряжение", 3, allow_stress, "МПа", "Балка: напряжение")):
        if limit is None:
            continue
        _positive(**{key: limit})
        values = [r[idx] for r in rows]
        status, worst = judge(values, limit)
        if len(rows) == 1:
            said = f"{values[0]:.3g} {unit}"
        else:
            said = (f"{min(values):.3g}…{max(values):.3g} {unit} "
                    f"({' — '.join(label[r[0]] for r in rows)})")
        text = {"pass": "в допуске", "fail": "БОЛЬШЕ ДОПУСТИМОГО",
                "warn": "порог ВНУТРИ вилки: решает жёсткость опор — "
                        "уточните закрепление или посчитайте его"}[status]
        out.append(_mk(f"{name}.{key}", title, status,
                       f"{name}: {key} {said} против {limit:g} — {text} "
                       f"({label.get(support, 'заделка…шарнир')}, {scheme}; {note})",
                       value=worst, unit=unit, op="<=", threshold=limit, duration_ms=ms))
    return out


# ------------------------------------------------------------------ подшипник
def bearing_static(*, c0: float, radial: float, axial: float = 0.0, min_s0: float = 1.0,
                   bore: float | None = None, light_series: bool = False,
                   static_equivalent: float | None = None,
                   c: float | None = None, rpm: float | None = None,
                   min_hours: float | None = None, equivalent_load: float | None = None,
                   kind: str = "ball", name: str = "подшипник") -> list[CheckResult]:
    """Подшипник качения: статический запас, осевая сила, по запросу ресурс.

        P₀ = max(Fr, 0.6·Fr + 0.5·Fa)      радиальный шариковый (SKF)
        s₀ = C₀ / P₀                         < 1 мало, 1…2 спокойно, ≥ 2 удары
        Fa ≤ 0.5·C₀, а у малых (отверстие до 12 мм) и лёгких серий ≤ 0.25·C₀
        L10h = (C/P)^p · 10⁶ / (60·n),  p = 3 шариковый, 10/3 роликовый

    Для медленных и ручных узлов (большинство прототипов) статики достаточно.
    Ресурс — только с эквивалентной нагрузкой P: при осевой силе она требует
    коэффициентов X, Y из каталога, и угадывать их здесь нельзя.
    """
    t0 = time.perf_counter()
    _positive(c0=c0, radial=radial, min_s0=min_s0)
    if axial < 0:
        raise ValueError(f"осевая сила {axial:g}: ожидается неотрицательная")
    if kind not in ("ball", "roller"):
        raise ValueError(f"тип — 'ball' или 'roller', получено {kind!r}")
    if static_equivalent is not None:
        _positive(static_equivalent=static_equivalent)
        p0 = static_equivalent
        how = "P₀ задана"
    elif kind == "ball":
        p0 = max(radial, 0.6 * radial + 0.5 * axial)
        how = "P₀ = max(Fr, 0.6·Fr + 0.5·Fa)"
    elif axial == 0:
        p0, how = radial, "P₀ = Fr"
    else:
        raise ValueError("у роликового под осевой силой P₀ зависит от угла контакта — "
                         "задайте static_equivalent из каталога")
    s0 = c0 / p0
    ms = (time.perf_counter() - t0) * 1000
    out = [_mk(f"{name}.статика", "Подшипник: статический запас",
               "pass" if s0 >= min_s0 else "fail",
               f"{name}: {how} = {p0:.0f} Н, s₀ = C₀/P₀ = {c0:.0f}/{p0:.0f} = {s0:.2f} "
               f"{'≥' if s0 >= min_s0 else '<'} {min_s0:g}"
               + ("" if s0 >= min_s0 else " — вмятины на дорожках")
               + ". Наружное кольцо должно быть опёрто по всей окружности: без опоры "
                 "оно гнётся между телами качения и лопается задолго до C₀",
               value=s0, unit="доля", op=">=", threshold=min_s0, duration_ms=ms)]
    if axial > 0 and kind == "ball":
        small = light_series or (bore is not None and bore <= 12)
        share = 0.25 if small else 0.5
        limit = share * c0
        ok = axial <= limit
        out.append(_mk(
            f"{name}.осевая", "Подшипник: осевая сила", "pass" if ok else "fail",
            f"{name}: осевая {axial:.0f} Н {'≤' if ok else '>'} {share:g}·C₀ = {limit:.0f} Н"
            + (" (малый или лёгкой серии — предел 0.25·C₀)" if small else "")
            + ("" if ok else ". Под такую осевую — упорный или радиально-упорный"),
            value=axial, unit="Н", op="<=", threshold=limit, duration_ms=ms))
    if min_hours is not None:
        if c is None or rpm is None:
            raise ValueError("для ресурса нужны c (динамическая грузоподъёмность, Н) и rpm")
        _positive(c=c, rpm=rpm, min_hours=min_hours)
        if equivalent_load is None:
            if axial > 0:
                raise ValueError("при осевой силе эквивалентная нагрузка P = X·Fr + Y·Fa "
                                 "требует X, Y из каталога — задайте equivalent_load")
            equivalent_load = radial
        _positive(equivalent_load=equivalent_load)
        p = 3.0 if kind == "ball" else 10.0 / 3.0
        hours = (c / equivalent_load) ** p * 1e6 / (60 * rpm)
        ok = hours >= min_hours
        out.append(_mk(
            f"{name}.ресурс", "Подшипник: ресурс", "pass" if ok else "fail",
            f"{name}: L10h = (C/P)^{p:.3g}·10⁶/(60·n) = ({c:.0f}/{equivalent_load:.0f})"
            f"^{p:.3g}·10⁶/(60·{rpm:g}) = {hours:.0f} ч {'≥' if ok else '<'} {min_hours:g}",
            value=hours, unit="ч", op=">=", threshold=min_hours, duration_ms=ms))
    return out


# --------------------------------------------------------------- лишние связи
#: Сколько связей отнимает соединение (класс кинематической пары).
JOINT_CONSTRAINTS = {
    "revolute": 5, "prismatic": 5, "screw": 5,      # шарнир с упором, ползун, винт
    "cylindrical": 4,                                # вал во втулке без упора
    "spherical": 3, "planar": 3,                     # шаровой, плоскость по плоскости
    "line": 2,                                       # цилиндр по плоскости
    "point": 1,                                      # шар по плоскости, ролик по валу
}


def redundant_constraints(*, links: int, mobility: int, joints: list,
                          allowed: int = 0, name: str = "механизм") -> CheckResult:
    """Лишние связи по формуле Малышева — откуда берутся натяг и заедание.

        q = W − 6·n + Σ k·p_k

    n — подвижные звенья, W — сколько подвижностей нужно на самом деле
    (включая местные: вращение ролика вокруг своей оси), p_k — соединения,
    отнимающие k связей. q = 0 — узел собирается без натяга при любых
    неточностях в пределах ходов; каждая единица q — требование к точности
    изготовления. Каретка на двух валах с двумя втулками: q = 3 —
    межосевое и параллельность в двух плоскостях, поэтому такие и клинят.

    `joints` — список {name, kind} (kind из JOINT_CONSTRAINTS) или
    {name, removes: k}. `allowed` — сколько лишних связей введено НАМЕРЕННО:
    сделано упругими и регулируемыми (поджатый ролик, преднатяг). q < 0 —
    подвижностей больше, чем объявлено: узел может «подпрыгнуть».
    """
    t0 = time.perf_counter()
    links = _whole(links, "links", 1)
    mobility = _whole(mobility, "mobility", 1)
    allowed = _whole(allowed, "allowed", 0)
    if not isinstance(joints, list) or not joints:
        raise ValueError("joints — непустой список соединений")
    total, parts = 0, []
    for j in joints:
        if not isinstance(j, dict):
            raise ValueError(f"соединение описывается словарём {{name, kind}}, получено {j!r}")
        label = str(j.get("name", "?"))
        if "removes" in j:
            k = _whole(j["removes"], f"{label}: removes", 1, 5)
        else:
            kind = j.get("kind")
            if kind not in JOINT_CONSTRAINTS:
                raise ValueError(f"{label}: kind — один из {tuple(JOINT_CONSTRAINTS)}, "
                                 f"получено {kind!r}")
            k = JOINT_CONSTRAINTS[kind]
        total += k
        parts.append(f"{label} {k}")
    q = mobility - 6 * links + total
    formula = (f"q = W − 6n + Σk·p = {mobility} − 6·{links} + ({' + '.join(parts)}) = {q}")
    if q < 0:
        status = "warn"
        text = (f"подвижностей на {-q} больше объявленных: узел не определён — может "
                f"подпрыгнуть или провернуться при смене направления сил")
    elif q <= allowed:
        status = "pass"
        text = ("лишних связей нет — самоустанавливается" if q == 0 else
                f"лишних связей {q}, все объявлены намеренными (упругие, регулируемые)")
    else:
        status = "fail"
        text = (f"лишних связей {q}" + (f", намеренных из них {allowed}" if allowed else "")
                + ": каждая — требование к точности, невыполненное даёт натяг, заедание "
                  "и износ. Назовите каждую словами и решите: убрать (самоустановка, "
                  "плавающая гайка, одна база), сделать упругой и регулируемой — или "
                  "обеспечить точностью и оценить её цену")
    return _mk(f"{name}.связи", "Лишние связи", status, f"{name}: {formula} — {text}",
               value=float(q), unit="шт", op="<=", threshold=float(allowed),
               duration_ms=(time.perf_counter() - t0) * 1000)
