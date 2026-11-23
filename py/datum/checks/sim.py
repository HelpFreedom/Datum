"""Проверки по симуляции механизма (MuJoCo + геометрия Datum).

Каждая берёт ОДИН прогон сценария (`Project.simulate`, общий для всех
проверок этого сценария) и судит своё: хватает ли приводов, успевают ли они,
устоит ли основание, не задевают ли звенья. Числа — из `sim.run`; здесь
только пороги и слова.
"""

from __future__ import annotations

import math
import time

import numpy as np

from datum.checks.result import CheckResult

__all__ = ["sim_drive", "sim_tracking", "sim_base", "sim_clearance", "sim_loads", "sim_backlash",
           "sim_stress"]


def _who(comp, result) -> str:
    return f"{getattr(comp.mechanism, '_name', '') or 'механизм'} «{result.scenario}»"


def _when(result, i: int) -> str:
    joints = result.plan.joints
    q = ", ".join(f"{j} {result.plan.q[i, joints.index(j)]:.1f}" for j in result.plan.drivers)
    return f"t = {result.plan.t[i]:.2f} с ({q})"


def sim_drive(sim, *, margin: float = 1.2, id: str = "", name: str = "") -> list[CheckResult]:
    """Привод каждого сустава даёт нужный момент с запасом: предел / пик ≥ margin."""
    t0 = time.perf_counter()
    comp, r = sim
    who = name or _who(comp, r)
    out = []
    for k, j in enumerate(r.plan.drivers):
        joint = comp.mechanism.links[j].joint
        need = np.abs(r.required[:, k])
        i = int(np.argmax(need))
        peak = float(need[i])
        static = float(np.max(np.abs(r.required[:, k][np.all(r.plan.qd == 0, axis=1)]))
                       if np.any(np.all(r.plan.qd == 0, axis=1)) else 0.0)
        u = joint.effort_unit
        if joint.drive is None:
            out.append(CheckResult(
                id=f"{id or 'sim.привод'}.{j}", title="Момент привода", group="sim",
                status="warn", severity="info", value=peak, unit=u, op="none",
                message=(f"{who}: {j} — привода нет; суставу нужно до {peak:.3g} {u} "
                         f"({_when(r, i)}), в покое до {static:.3g} {u}"),
                inputs=tuple(), duration_ms=(time.perf_counter() - t0) * 1000))
            continue
        lim = joint.drive.limit
        ratio = lim / peak if peak > 1e-12 else math.inf
        ok = ratio >= margin
        extras = []
        if r.required_rigid is not None:
            rigid = float(np.max(np.abs(r.required_rigid[:, k])))
            if abs(rigid - peak) > 1e-9 * max(rigid, 1e-12):
                extras.append(f"без трения и КПД было бы {rigid:.3g}")
        if joint.drive.self_locking:
            extras.append("в покое держит самоторможение передачи")
        note = f"; {', '.join(extras)}" if extras else ""
        out.append(CheckResult(
            id=f"{id or 'sim.привод'}.{j}", title="Момент привода", group="sim",
            status="pass" if ok else "fail", value=ratio, unit="×", op=">=", threshold=margin,
            message=(f"{who}: {j} — нужно до {peak:.3g} {u} ({_when(r, i)}; в покое до "
                     f"{static:.3g}{note}), привод {lim:.3g} {u}: запас {ratio:.2f}× "
                     + ("≥" if ok else "<") + f" {margin:g}"
                     + ("" if ok else " — НЕ ХВАТИТ: легче звенья дальше от оси (заполнение, "
                        "стенки), короче плечо, противовес или привод сильнее")),
            duration_ms=(time.perf_counter() - t0) * 1000))
    return out


def sim_tracking(sim, *, max_error: float = 2.0, id: str = "", name: str = "") -> list[CheckResult]:
    """Прямой прогон: приводы с пределом момента идут за планом, отставание ≤ max_error (°, мм)."""
    t0 = time.perf_counter()
    comp, r = sim
    who = name or _who(comp, r)
    if r.tracking is None:
        return [CheckResult(id=id or "sim.слежение", title="Слежение приводов", group="sim",
                            status="skip", severity="info",
                            message=f"{who}: прямого прогона нет — " + "; ".join(r.notes),
                            duration_ms=(time.perf_counter() - t0) * 1000)]
    out = []
    # Прямой прогон идёт по ЖЁСТКОЙ модели без потерь: трения опор и КПД
    # передачи в нём нет. Когда они объявлены, настоящее отставание больше, а
    # хватит ли момента с потерями — отвечает `sim_drive`. Сказать это обязана
    # сама проверка: иначе зелёное «слежение» читается как запас с потерями.
    lossy = any((jt.friction is not None)
                or (jt.drive is not None and getattr(jt.drive, "efficiency", 1.0) < 1.0)
                for jt in (lk.joint for lk in comp.mechanism.links.values()) if jt is not None)
    caveat = (" (прогон без трения опор и потерь передачи — с ними отставание больше; "
              "запас момента с потерями — в sim_drive)" if lossy else "")
    for k, j in enumerate(r.plan.drivers):
        err = np.abs(r.tracking[:, k])
        i = int(np.argmax(err))
        joint = comp.mechanism.links[j].joint
        sat = float(np.mean(np.abs(r.applied[:, k]) >= 0.999 * joint.drive.limit)) if r.applied is not None else 0.0
        ok = float(err[i]) <= max_error
        out.append(CheckResult(
            id=f"{id or 'sim.слежение'}.{j}", title="Слежение приводов", group="sim",
            status="pass" if ok else "fail", value=float(err[i]), unit=joint.unit, op="<=",
            threshold=max_error,
            message=(f"{who}: {j} — наибольшее отставание {err[i]:.2f} {joint.unit} ({_when(r, i)}), "
                     f"привод на пределе {sat * 100:.0f} % времени{caveat}"
                     + ("" if ok else " — НЕ УСПЕВАЕТ: медленнее сценарий (speed_scale), "
                        "плавнее разгон или привод сильнее")),
            duration_ms=(time.perf_counter() - t0) * 1000))
    return out


def sim_base(sim, *, min_margin_mm: float = 5.0, max_slip: float = 0.8,
             max_force_n: float = math.inf, max_moment_nm: float = math.inf,
             id: str = "", name: str = "") -> list[CheckResult]:
    """Устоит ли изделие в движении — или сколько держать его креплению."""
    t0 = time.perf_counter()
    comp, r = sim
    who = name or _who(comp, r)
    ms = lambda: (time.perf_counter() - t0) * 1000             # noqa: E731
    if comp.mechanism.base == "resting":
        i = int(np.argmin(r.tip_margin_mm))
        tip = float(r.tip_margin_mm[i])
        lifted = not math.isfinite(tip)
        ok = (not lifted) and tip >= min_margin_mm
        j = int(np.argmax(r.slip_ratio))
        slip = float(r.slip_ratio[j])
        return [
            CheckResult(id=f"{id or 'sim.основание'}.опрокидывание", title="Устойчивость в движении",
                        group="sim", status="pass" if ok else "fail",
                        value=tip if not lifted else -1e9, unit="мм", op=">=", threshold=min_margin_mm,
                        message=(f"{who}: центр давления в {tip:.1f} мм от края опоры ({_when(r, i)})"
                                 if not lifted else f"{who}: основание ОТРЫВАЕТСЯ от стола ({_when(r, i)})")
                        + ("" if ok else " — ОПРОКИНЕТСЯ: шире опора, тяжелее основание, "
                           "медленнее разгон или груз ближе"),
                        duration_ms=ms()),
            CheckResult(id=f"{id or 'sim.основание'}.скольжение", title="Основание не едет",
                        group="sim", status="pass" if slip <= max_slip else "fail",
                        value=slip, unit="", op="<=", threshold=max_slip,
                        message=(f"{who}: горизонтальная сила — {slip:.2f} от трения покоя "
                                 f"(μ = {comp.mechanism.friction:g}, {_when(r, j)})"
                                 + ("" if slip <= max_slip else " — ПОЕДЕТ: резиновые ножки, "
                                    "тяжелее основание или плавнее разгон")),
                        duration_ms=ms()),
        ]
    f = np.linalg.norm(r.base_force, axis=1)
    mo = np.linalg.norm(r.base_moment, axis=1)
    i, j = int(np.argmax(f)), int(np.argmax(mo))
    ok_f, ok_m = float(f[i]) <= max_force_n, float(mo[j]) <= max_moment_nm
    return [CheckResult(
        id=f"{id or 'sim.основание'}.крепление", title="Нагрузка на крепление основания", group="sim",
        status="pass" if ok_f and ok_m else "fail", value=float(mo[j]), unit="Н·м", op="<=",
        threshold=max_moment_nm,
        message=(f"{who}: крепление держит до {f[i]:.1f} Н ({_when(r, i)}) и до {mo[j]:.2f} Н·м "
                 f"опрокидывающего момента ({_when(r, j)})"
                 + ("" if ok_f and ok_m else " — БОЛЬШЕ ДОПУСТИМОГО для крепления")),
        duration_ms=ms())]


def sim_clearance(sim, *, id: str = "", name: str = "") -> CheckResult:
    """Звенья не задевают друг друга по ходу сценария (точная геометрия)."""
    t0 = time.perf_counter()
    comp, r = sim
    who = name or _who(comp, r)
    hits = r.clearance
    how = getattr(r, "clearance_info", None) or {}
    step = how.get("step_mm")
    said = (f"; сверено с шагом {step:g} мм, {how.get('checks', 0)} сверок" if step else "")
    if not hits:
        from datum.sim.run import CLEARANCE_STEP_MM

        coarse = bool(step) and step > CLEARANCE_STEP_MM + 1e-9
        # Огрублённый шаг — не «прошло»: тонкое препятствие могло остаться
        # между позами. Говорим об этом вердиктом, а не только заметкой.
        return CheckResult(id=id or "sim.задевания", title="Задевания по траектории", group="sim",
                           status="warn" if coarse else "pass",
                           value=0.0, unit="мм³", op="<=", threshold=0.5,
                           message=f"{who}: звенья не задевают друг друга по ходу сценария "
                                   f"(сверх касаний в нулевой позе){said}"
                                   + (" — шаг огрублён из-за длины хода: препятствие "
                                      "тоньше шага могло быть пропущено, разбейте сценарий"
                                      if coarse else ""),
                           duration_ms=(time.perf_counter() - t0) * 1000)
    h = hits[0]
    pose = ", ".join(f"{k} {v:g}" for k, v in h["q"].items())
    more = f"; ещё {len(hits) - 1} пар" if len(hits) > 1 else ""
    return CheckResult(id=id or "sim.задевания", title="Задевания по траектории", group="sim",
                       status="fail", value=h["volume_mm3"], unit="мм³", op="<=", threshold=0.5,
                       message=(f"{who}: {h['a']} задевает {h['b']} — {h['volume_mm3']:.1f} мм³ "
                                f"при t = {h['t']:.2f} с ({pose}){more}"),
                       duration_ms=(time.perf_counter() - t0) * 1000)


def sim_loads(sim, *, margin: float = 1.0, id: str = "", name: str = "") -> list[CheckResult]:
    """Опоры суставов несут реакции динамики: допустимая / наибольшая ≥ margin."""
    t0 = time.perf_counter()
    comp, r = sim
    who = name or _who(comp, r)
    out = []
    labels = ("радиальная", "осевая", "момент поперёк оси")
    units = ("Н", "Н", "Н·м")
    for k, j in enumerate(r.plan.joints):
        cap = comp.mechanism.links[j].joint.capacity
        if cap is None or r.reactions is None:
            continue
        worst, what = math.inf, ""
        for col, lim in enumerate((cap.radial, cap.axial, cap.moment)):
            if lim <= 0:
                continue
            series = r.reactions[:, k, col]
            i = int(np.argmax(series))
            ratio = lim / series[i] if series[i] > 1e-12 else math.inf
            if ratio < worst:
                worst = ratio
                what = (f"{labels[col]} {series[i]:.3g} из {lim:.3g} {units[col]} "
                        f"({_when(r, i)})")
        if not what:
            continue
        ok = worst >= margin
        out.append(CheckResult(
            id=f"{id or 'sim.опоры'}.{j}", title="Нагрузка на опору сустава", group="sim",
            status="pass" if ok else "fail", value=worst, unit="×", op=">=", threshold=margin,
            message=(f"{who}: {j} — {what}: запас {worst:.2f}× " + ("≥" if ok else "<")
                     + f" {margin:g}" + ("" if ok else " — ОПОРА ПЕРЕГРУЖЕНА: груз ближе, легче "
                                         "дальнее, вторая опора или подшипник крупнее")),
            duration_ms=(time.perf_counter() - t0) * 1000))
    if not out:
        out.append(CheckResult(id=id or "sim.опоры", title="Нагрузка на опору сустава", group="sim",
                               status="skip", severity="info",
                               message=f"{who}: ни у одного сустава не задано capacity(…)",
                               duration_ms=(time.perf_counter() - t0) * 1000))
    return out


def sim_backlash(sim, *, max_mm: float = math.inf, id: str = "", name: str = "") -> CheckResult:
    """Люфт передач: потерянный ход в точках грузов ≤ max_mm и где зазор перескакивается."""
    t0 = time.perf_counter()
    comp, r = sim
    who = name or _who(comp, r)
    lash = r.backlash or {}
    if not lash.get("joints"):
        return CheckResult(id=id or "sim.люфт", title="Люфт передач", group="sim", status="skip",
                           severity="info", message=f"{who}: люфт приводов не задан (drive(backlash=…))",
                           duration_ms=(time.perf_counter() - t0) * 1000)
    lost = lash.get("lost_motion_mm") or {}
    worst = max(lost.values()) if lost else 0.0
    rev = lash.get("reversals", [])
    where = ", ".join(f"{e['joint']} t={e['t']:.2f} с" for e in rev[:4]) + ("…" if len(rev) > 4 else "")
    ok = worst <= max_mm
    msg = (f"{who}: потерянный ход в точке груза до {worst:.2f} мм" if lost else
           f"{who}: грузов нет — потерянный ход не к чему отнести")
    msg += (f"; зазор перескакивается {len(rev)} раз ({where}) — там стук и рывок"
            if rev else "; момент знака не меняет — зазор всё время выбран в одну сторону")
    return CheckResult(id=id or "sim.люфт", title="Люфт передач", group="sim",
                       status="pass" if ok else "fail",
                       severity="blocking" if math.isfinite(max_mm) else "info",
                       value=worst, unit="мм", op="<=",
                       threshold=max_mm if math.isfinite(max_mm) else None,
                       message=msg + ("" if ok else f" — больше {max_mm:g} мм"),
                       duration_ms=(time.perf_counter() - t0) * 1000)


#: Невязка равновесия МКЭ звена (доля приложенного), выше которой расчёту не
#: верят: у исправного расчёта она ~1e-8 (тест `test_fem_loads`).
STRESS_IMBALANCE_MAX = 1e-3
#: Расхождение приложенной к звену нагрузки с реакцией из MuJoCo, выше
#: которого запас сообщается с оговоркой. Несколько процентов законны: центр
#: масс печати не совпадает с центром объёма (см. шапку `sim/stress.py`).
STRESS_MISMATCH_WARN = 0.10


def sim_stress_verdict(rep, *, who: str, strength_z=None, strength_xy=None, margin: float = 1.0,
                       id: str = "", t0: float | None = None) -> CheckResult:
    """Вердикт по отчёту `sim.stress.StressReport`."""
    t0 = time.perf_counter() if t0 is None else t0
    lk = rep.link
    base = id or "sim.напряжения"
    where = ", ".join(f"{v:.0f}" for v in rep.peak_at)
    head = (f"{who}: {lk} ({', '.join(rep.pieces)}) в t = {rep.t:.2f} с — Мизес "
            f"{rep.value_mpa:.3g} МПа ({rep.basis}; пик в ({where}) мм в системе звена), "
            f"прогиб до {rep.max_displacement_mm:.3f} мм")
    tail = "".join(f"\n    ({n})" for n in rep.notes)
    # Самопроверки расчёта — в вердикт. Прежде они считались и лежали в ответе
    # двумя числами, которых никто не судил: потерянное пятно нагрузки или
    # ошибка знака давали обычное «pass, запас N×» (аудит 2026-11-21).
    unbalanced = max((abs(float(v)) for v in (getattr(rep, "imbalance", None) or ())),
                     default=0.0)
    mismatch = abs(float(getattr(rep, "mujoco_mismatch", 0.0) or 0.0))
    if unbalanced > STRESS_IMBALANCE_MAX:
        return CheckResult(
            id=f"{base}.{lk}", title="Напряжения в звене", group="sim", status="error",
            value=unbalanced, unit="доля", op="<=", threshold=STRESS_IMBALANCE_MAX,
            message=(head + f" — РАСЧЁТУ ВЕРИТЬ НЕЛЬЗЯ: приложенное к звену не "
                     f"уравновешено реакцией опоры (невязка {unbalanced * 100:.2g} % "
                     f"приложенного, допустимо {STRESS_IMBALANCE_MAX * 100:g} %) — часть "
                     f"нагрузки потеряна или приложена не туда. Проверьте, что детали "
                     f"звена касаются друг друга и опоры (scene_view, section)" + tail),
            duration_ms=(time.perf_counter() - t0) * 1000)
    doubt = ""
    if mismatch > STRESS_MISMATCH_WARN:
        doubt = (f"\n    (! нагрузка на звено расходится с динамикой MuJoCo на "
                 f"{mismatch * 100:.0f} % — больше {STRESS_MISMATCH_WARN * 100:g} %: "
                 f"центр масс или навесные тела учтены по-разному, запас ниже — "
                 f"с этой оговоркой)")
    elif mismatch > 0.01:
        doubt = f"\n    (сверка с MuJoCo: расхождение нагрузки {mismatch * 100:.1f} %)"
    tail += doubt
    if not strength_z:
        return CheckResult(id=f"{base}.{lk}", title="Напряжения в звене", group="sim",
                           status="warn", severity="info", value=rep.value_mpa, unit="МПа",
                           op="none", message=head + "; предела материала нет "
                           "(MAT_TENSILE_Z) — судить не с чем" + tail,
                           duration_ms=(time.perf_counter() - t0) * 1000)
    strength_z = float(strength_z)
    ratio = strength_z / max(rep.value_mpa, 1e-12)
    # огромный запас — числом с тремя значащими, а не «105745.3×»
    ratio_txt = f"{ratio:.3g}" if ratio < 1000 else "больше 1000"
    ok = ratio >= margin
    xy = (f", вдоль слоёв {float(strength_xy):g} МПа — "
          f"{min(float(strength_xy) / max(rep.value_mpa, 1e-12), 1000):.3g}×"
          + ("+" if float(strength_xy) / max(rep.value_mpa, 1e-12) > 1000 else "")
          if strength_xy else "")
    return CheckResult(
        id=f"{base}.{lk}", title="Напряжения в звене", group="sim",
        status=("fail" if not ok else "warn" if mismatch > STRESS_MISMATCH_WARN else "pass"),
        value=ratio, unit="×", op=">=", threshold=margin,
        message=(head + f"; предел между слоями {strength_z:g} МПа: запас {ratio_txt}× "
                 + ("≥" if ok else "<") + f" {margin:g}{xy}"
                 + ("" if ok else " — ЗВЕНО НЕ ВЫДЕРЖИТ: толще стенка у пика, скругление, "
                    "больше периметров (walls) или поворот печати, чтобы растяжение шло "
                    "вдоль слоёв") + tail),
        duration_ms=(time.perf_counter() - t0) * 1000)


def sim_stress(sim, *, link: str | None = None, modulus: float | None = None,
               strength_z: float | None = None, strength_xy: float | None = None,
               poisson: float = 0.35, edge: float = 0.0, margin: float = 1.0,
               id: str = "", name: str = "") -> list[CheckResult]:
    """Печатное звено выдерживает худший момент прогона: предел между слоями /
    напряжение ≥ margin (МКЭ по нагрузкам динамики, `sim.stress`)."""
    from datum.sim.mechanism import MechanismError
    from datum.sim.stress import link_stress, mesh_links, stress_candidates

    t0 = time.perf_counter()
    comp, r = sim
    who = name or _who(comp, r)
    links = [link] if link else stress_candidates(comp)
    base = id or "sim.напряжения"
    if modulus is None:
        return [CheckResult(id=base, title="Напряжения в звене", group="sim", status="skip",
                            severity="info",
                            message=(f"{who}: модуля материала нет (MAT_E: [printer] material в "
                                     f"datum.toml или modulus=…) — МКЭ не считается"),
                            duration_ms=(time.perf_counter() - t0) * 1000)]
    out = []
    try:
        prepared = mesh_links(comp, links, edge=edge)       # все сетки разом, на все ядра
    except Exception:                                       # noqa: BLE001
        prepared = {}                                       # причина — у каждого звена ниже
    for lk in links:
        try:
            rep = link_stress(comp, r, lk, modulus=float(modulus), poisson=poisson,
                              prepared=prepared.get(lk),
                              strength_z=None if strength_z is None else float(strength_z),
                              strength_xy=None if strength_xy is None else float(strength_xy),
                              edge=edge)
        except MechanismError as e:
            out.append(CheckResult(id=f"{base}.{lk}", title="Напряжения в звене", group="sim",
                                   status="error", message=f"{who}: {lk} — {e}",
                                   duration_ms=(time.perf_counter() - t0) * 1000))
            continue
        out.append(sim_stress_verdict(rep, who=who, strength_z=strength_z, strength_xy=strength_xy,
                                      margin=margin, id=id, t0=t0))
    if not out:
        from datum.sim.stress import why_no_candidates

        out.append(CheckResult(id=base, title="Напряжения в звене", group="sim", status="skip",
                               severity="info", message=f"{who}: {why_no_candidates(comp)}",
                               duration_ms=(time.perf_counter() - t0) * 1000))
    return out
