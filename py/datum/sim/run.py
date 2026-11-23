"""Прогон сценария: план движения, нужные моменты, реакция основания, прямой
прогон с пределами приводов и задевания по траектории.

ЧТО СЧИТАЕТСЯ И ПОЧЕМУ ТАК.

1. ПЛАН. Суставы едут СИНХРОННО (все приходят в позу вместе), профиль —
   минимального рывка (полином 5-й степени): у него конечное ускорение и
   нулевой рывок на концах, как у плавного пуска прошивок. Длительность
   отрезка — наименьшая, при которой ни один сустав не превышает скорости и
   ускорения своего привода (пики профиля: 1.875·Δ/T и 5.774·Δ/T²).

2. НУЖНЫЙ МОМЕНТ — обратная динамика MuJoCo (`mj_inverse`) вдоль плана:
   тяжесть звеньев и груза, разгон, кориолисовы силы — всё, что сустав
   обязан дать, чтобы механизм шёл по плану. Без регулятора, без подбора
   коэффициентов: ответ про механизм, а не про настройку. Сверено на
   маятнике и двухзвеннике с формулами (tests/unit/test_sim.py).

3. ОСНОВАНИЕ — та же обратная динамика, но основание на свободном суставе с
   нулевым ускорением: шесть чисел реакции — что опора обязана дать, чтобы
   основание не сдвинулось. Стоит на столе (`resting`) — центр давления
   против опорного многоугольника (опрокинется ли) и горизонталь против
   трения (поедет ли); закреплено (`clamped`) — силы и моменты, которые
   держит крепление.

4. ПРЯМОЙ ПРОГОН — приводы с ПРЕДЕЛОМ момента следуют плану; где нужного
   момента больше предела, сустав отстаёт. Отставание — то, что увидит
   человек на настоящем железе: «не дотягивает», «проседает в конце хода».

5. ЗАДЕВАНИЯ — точная геометрия Datum в позах вдоль плана: пересечение
   звеньев сверх того, что было в нулевой позе (касания по замыслу: ось в
   отверстии, винт в гнезде).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from datum.sim.compile import Compiled
from datum.sim.mechanism import MechanismError, Scenario

__all__ = ["Plan", "plan_motion", "SimResult", "run_scenario", "poses_at", "link_matrix", "DEFAULT_SPEED",
           "PLAN_DT_S", "MAX_FRAMES"]

#: Скорость сустава без привода (°/с, мм/с): в механизме из assembly.yaml
#: привода нет, а план нужен. Сказано в отчёте.
DEFAULT_SPEED = 30.0
#: Шаг отсчётов плана для обратной динамики, с.
PLAN_DT_S = 0.005
#: Кадров для проигрывателя окна — не больше.
MAX_FRAMES = 400
#: Полоса поправки регулятора прямого прогона, Гц: типичная для сервопривода и
#: устойчивая при шаге 1 мс (ω·dt ≈ 0.03). Упреждение несёт всё движение, поправка
#: лишь гасит отклонение — поэтому ошибка видна только при нехватке момента.
SERVO_HZ = 5.0
#: Профиль минимального рывка: пик скорости 1.875·Δ/T, ускорения 5.7735·Δ/T².
_V_PEAK, _A_PEAK = 1.875, 10.0 / math.sqrt(3.0)


@dataclass
class Plan:
    joints: list[str]                # все суставы модели (с ведомыми)
    t: np.ndarray                    # с
    q: np.ndarray                    # (n, J) — °, мм
    qd: np.ndarray                   # °/с, мм/с
    qdd: np.ndarray                  # °/с², мм/с²
    pose_t: list[float]              # моменты прихода в позы
    notes: list[str] = field(default_factory=list)
    #: независимые суставы (их задаёт сценарий) и матрица связи: q_все = q_незав · Cᵀ
    drivers: list[str] = field(default_factory=list)
    coupling: np.ndarray | None = None


def _min_jerk(s: np.ndarray):
    p = 10 * s ** 3 - 15 * s ** 4 + 6 * s ** 5
    dp = 30 * s ** 2 - 60 * s ** 3 + 30 * s ** 4
    ddp = 60 * s - 180 * s ** 2 + 120 * s ** 3
    return p, dp, ddp


def plan_motion(comp: Compiled, scenario: Scenario, *, dt: float = PLAN_DT_S) -> Plan:
    """План по НЕЗАВИСИМЫМ суставам; ведомые (шестерня за сердцем) — через
    матрицу связи, и их ход сверяется с объявленным."""
    mech = comp.mechanism
    joints = mech.drivers()
    notes: list[str] = []
    speed, accel = {}, {}
    for j in joints:
        d = mech.links[j].joint.drive                       # type: ignore[union-attr]
        if d is None:
            speed[j], accel[j] = DEFAULT_SPEED, DEFAULT_SPEED / 0.25
            notes.append(f"у сустава {j} нет привода: план — {DEFAULT_SPEED:g} "
                         f"{mech.links[j].joint.unit}/с с разгоном 0.25 с")
        else:
            speed[j], accel[j] = d.speed, d.max_accel
        speed[j] *= scenario.speed_scale
        accel[j] *= scenario.speed_scale ** 2
    cur = {j: 0.0 for j in joints}
    first = scenario.poses[0]
    # Сустав, не названный в первой позе, стоит в нуле. Если ноль вне его хода,
    # упор сустава отдаёт свою силу как «нужный момент» — 316 Н·м вместо 0.61
    # (аудит 2026-11-21): бессмысленное число вместо отказа.
    for j in joints:
        jt = mech.links[j].joint
        lo, hi = jt.limits
        if j not in first and not (lo - 1e-9 <= 0.0 <= hi + 1e-9):
            from datum.sim.mechanism import MechanismError

            raise MechanismError(
                f"сценарий «{scenario.name}»: сустав {j} не назван в первой позе, а его "
                f"ноль вне хода [{lo:g}, {hi:g}] {jt.unit} — назовите положение "
                f"{j} в первой позе")
    cur.update(first)
    t_all, q_all, qd_all, qdd_all, pose_t = [], [], [], [], []
    t0 = 0.0

    def emit_hold(duration):
        nonlocal t0
        n = max(1, int(round(duration / dt)))
        ts = t0 + np.arange(n) * dt
        t_all.append(ts)
        q_all.append(np.tile([cur[j] for j in joints], (n, 1)))
        qd_all.append(np.zeros((n, len(joints))))
        qdd_all.append(np.zeros((n, len(joints))))
        t0 = float(ts[-1] + dt)

    pose_t.append(0.0)
    emit_hold(scenario.hold_s)
    for pose in scenario.poses[1:]:
        target = dict(cur)
        target.update(pose)
        delta = {j: target[j] - cur[j] for j in joints}
        T = 0.0
        for j in joints:
            d = abs(delta[j])
            if d > 0:
                T = max(T, _V_PEAK * d / speed[j], math.sqrt(_A_PEAK * d / accel[j]))
        if T <= 0:
            pose_t.append(t0)
            emit_hold(scenario.hold_s)
            continue
        T = max(T, 0.05)
        n = max(2, int(math.ceil(T / dt)))
        s = np.arange(n) / n
        p, dp, ddp = _min_jerk(s)
        ts = t0 + s * T
        q0 = np.array([cur[j] for j in joints])
        dq = np.array([delta[j] for j in joints])
        t_all.append(ts)
        q_all.append(q0 + np.outer(p, dq))
        qd_all.append(np.outer(dp / T, dq))
        qdd_all.append(np.outer(ddp / T ** 2, dq))
        t0 = float(t0 + T)
        cur = target
        pose_t.append(t0)
        emit_hold(scenario.hold_s)
    c = mech.coupling()
    q_ind, qd_ind, qdd_ind = np.vstack(q_all), np.vstack(qd_all), np.vstack(qdd_all)
    q, qd, qdd = q_ind @ c.T, qd_ind @ c.T, qdd_ind @ c.T
    alln = mech.joints()
    for k, name in enumerate(alln):
        lk = mech.links[name].joint
        if lk.follows is None:
            continue
        lo, hi = lk.limits
        if q[:, k].min() < lo - 1e-6 or q[:, k].max() > hi + 1e-6:
            raise MechanismError(
                f"сценарий {scenario.name!r}: ведомый {name} уходит на "
                f"{q[:, k].min():g}…{q[:, k].max():g}{lk.unit} при ходе [{lo:g}, {hi:g}] — "
                f"расширьте ход ведомого или сократите сценарий")
    return Plan(alln, np.concatenate(t_all), q, qd, qdd, pose_t, notes,
                drivers=list(joints), coupling=c)


def _si(comp: Compiled, joints: list[str]) -> np.ndarray:
    """Множитель «° / мм → рад / м» по суставам."""
    return np.array([math.pi / 180.0 if comp.mechanism.links[j].joint.kind == "revolute"
                     else 1e-3 for j in joints])


@dataclass
class SimResult:
    scenario: str
    plan: Plan
    # Столбцы трёх рядов ниже — НЕЗАВИСИМЫЕ суставы (`plan.drivers`): ведомый
    # своего привода не имеет, его нагрузка приведена к ведущему.
    required: np.ndarray             # (n, D) — нужный момент/сила, Н·м | Н
    tracking: np.ndarray | None      # (n, D) — отставание прямого прогона, °|мм
    applied: np.ndarray | None       # (n, D) — отданный приводом момент
    base_force: np.ndarray           # (n, 3) — Н, от опоры на основание
    base_moment: np.ndarray          # (n, 3) — Н·м о начале сцены
    cop: np.ndarray | None           # (n, 2) — центр давления, мм (resting)
    tip_margin_mm: np.ndarray | None # (n,) — до края опоры, мм; < 0 — опрокидывание
    support: np.ndarray | None       # опорный многоугольник (k, 2), мм
    slip_ratio: np.ndarray | None    # |F_гор| / (μ·F_верт)
    clearance: list[dict]
    frames: dict                     # для окна: t и положения звеньев
    notes: list[str]
    #: То же, что `required`, но жёсткая модель без трения и КПД — для сравнения.
    required_rigid: np.ndarray | None = None
    #: Реакции суставов по `plan.joints`: (n, J, 3) — радиальная, осевая (Н), момент поперёк оси (Н·м).
    reactions: np.ndarray | None = None
    #: Трение в опорах по `plan.joints` (n, J), Н·м | Н — модуль.
    friction: np.ndarray | None = None
    #: Загрузка звеньев (0 — свободно, 1 — на пределе): имя → (n,).
    utilization: dict = field(default_factory=dict)
    #: Люфт: перескоки зазора [{"joint", "t"}] и потерянный ход в точках грузов, мм.
    backlash: dict = field(default_factory=dict)
    #: Как сверялись задевания: шаг (мм), поз и сверок — см. `clearance`.
    clearance_info: dict = field(default_factory=dict)


def _mj():
    from datum.sim.model import _mj as m

    return m()


def _inverse(model, comp: Compiled, plan: Plan, joints: list[str], *, free: bool):
    mujoco = _mj()
    data = mujoco.MjData(model)
    k = _si(comp, plan.joints)
    q, qd, qdd = plan.q * k, plan.qd * k, plan.qdd * k
    order = [plan.joints.index(j) for j in joints]
    base = 7 if free else 0
    vbase = 6 if free else 0
    out_j = np.zeros((len(plan.t), len(joints)))
    out_b = np.zeros((len(plan.t), 6))
    # Реакции суставов (без свободного основания): радиальная и осевая сила, момент
    # поперёк оси — СИ. `cfrc_int` — сила родителя на звено, приведённая к центру
    # масс корня дерева; переносим к точке оси. Сверено: в покое сила = m·g, а её
    # момент вокруг оси = момент обратной динамики (tests/unit/test_sim.py).
    react = np.zeros((len(plan.t), len(joints), 3))
    jid = [model.joint(j).id for j in joints] if not free else []
    for i in range(len(plan.t)):
        if free:
            data.qpos[:7] = [0, 0, 0, 1, 0, 0, 0]
            data.qvel[:6] = 0
            data.qacc[:6] = 0
        data.qpos[base:base + len(joints)] = q[i, order]
        data.qvel[vbase:vbase + len(joints)] = qd[i, order]
        data.qacc[vbase:vbase + len(joints)] = qdd[i, order]
        mujoco.mj_inverse(model, data)
        out_j[i] = data.qfrc_inverse[vbase:vbase + len(joints)]
        if free:
            out_b[i] = data.qfrc_inverse[:6]
        else:
            mujoco.mj_rnePostConstraint(model, data)
            for c, j in enumerate(jid):
                b = model.jnt_bodyid[j]
                f, t = data.cfrc_int[b][3:], data.cfrc_int[b][:3]
                o, a = data.xanchor[j], data.xaxis[j]
                t_o = t + np.cross(data.subtree_com[model.body_rootid[b]] - o, f)
                fa = float(f @ a)
                react[i, c] = (np.linalg.norm(f - fa * a), abs(fa),
                               np.linalg.norm(t_o - float(t_o @ a) * a))
    if free:
        return out_j, out_b
    return out_j, out_b, react


def _forward(model, comp: Compiled, plan: Plan, joints: list[str], ff: np.ndarray):
    # ff — нужные моменты ВСЕХ суставов (столбцы в порядке `joints`); ведомые
    # двигаются равенством в модели, а их нагрузку несёт ведущий: u = Cᵀ·u_все.
    """Прямой прогон: привод даёт момент «как нужно по плану» (упреждение из
    обратной динамики) плюс поправку по ошибке, но не больше своего предела.

    Так устроены регуляторы роботов (computed torque): при хватающем моменте
    звено идёт по плану почти без ошибки, и отставание — ТОЛЬКО там, где
    нужного момента больше предела. Простой позиционный регулятор отставал бы
    и при избытке момента — число было бы о настройке, а не о механизме
    (замерено: 1.4° при запасе 200×)."""
    mujoco = _mj()
    data = mujoco.MjData(model)
    k = _si(comp, plan.joints)
    order = [plan.joints.index(j) for j in joints]
    q_plan = plan.q[:, order] * k[order]
    qd_plan = plan.qd[:, order] * k[order]
    data.qpos[:len(joints)] = q_plan[0]
    mujoco.mj_forward(model, data)
    full = np.zeros((model.nv, model.nv))
    # Поправка — ЗАКОНОМ ВЫЧИСЛЯЕМОГО МОМЕНТА: ускорение ω²·e + 2ω·ė (полоса
    # SERVO_HZ, критическое демпфирование), умноженное на ПОЛНУЮ матрицу масс в
    # текущей позе. Своя жёсткость каждому суставу по его диагонали матрицы
    # раскачивала связанную цепь: на руке v4 плечо и локоть с первого шага
    # дрались ±предел при ошибке в сотые градуса; на examples/arm жёсткость
    # «предел на 1°» давала то же. С полной матрицей ошибка каждого сустава
    # гаснет сама по себе, как задумано.
    w = 2.0 * math.pi * SERVO_HZ
    drivers = plan.drivers
    c = plan.coupling[order, :]
    acts = []
    for a in range(model.nu):
        jname = model.joint(int(model.actuator_trnid[a, 0])).name
        acts.append((a, drivers.index(jname), float(model.actuator_ctrlrange[a, 1])))
    n = len(joints)
    didx = [joints.index(d) for d in drivers]
    track = np.zeros((len(plan.t), len(drivers)))
    applied = np.zeros((len(plan.t), len(drivers)))
    step = model.opt.timestep
    t = 0.0
    for i, ti in enumerate(plan.t):
        while t < ti - 1e-12:
            qr = np.array([np.interp(t, plan.t, q_plan[:, c]) for c in range(n)])
            vr = np.array([np.interp(t, plan.t, qd_plan[:, c]) for c in range(n)])
            fr = np.array([np.interp(t, plan.t, ff[:, c]) for c in range(n)])
            mujoco.mj_fullM(model, data, full)
            u_all = fr + full[:n, :n] @ (w * w * (qr - data.qpos[:n]) + 2.0 * w * (vr - data.qvel[:n]))
            u = u_all @ c
            for a, col, lim in acts:
                data.ctrl[a] = min(max(float(u[col]), -lim), lim)
            mujoco.mj_step(model, data)
            t += step
        track[i] = ((q_plan[i] - data.qpos[:n]) / k[order])[didx]
        for a, col, _ in acts:
            applied[i, col] = data.actuator_force[a]
    return track, applied


def _coupling_si(comp: Compiled, plan: Plan, joints: list[str]) -> np.ndarray:
    """Матрица связи в СИ: q_все[рад|м] = C_си · q_незав[рад|м], столбцы — `plan.drivers`,
    строки — `joints`. Рейка за шестерней связывает градусы с миллиметрами."""
    order = [plan.joints.index(j) for j in joints]
    k_all = _si(comp, joints)
    k_ind = _si(comp, plan.drivers)
    return (np.diag(k_all) @ plan.coupling[order, :]) / k_ind[None, :]


def _forward_reduced(model, comp: Compiled, plan: Plan, joints: list[str], ff: np.ndarray):
    """Прямой прогон механизма с ВЕДОМЫМИ суставами — в независимых координатах.

    Связь идеальной передачи линейна: q = C·q_нез, поэтому уравнения движения
    точно приводятся к ведущим: M_пр = Cᵀ·M·C, h_пр = Cᵀ·h (M и h — MuJoCo в
    текущей позе). Шаг — полунеявный Эйлер с шагом модели. Регулятор — тот же
    вычисляемый момент, что в `_forward`, но на приведённой матрице."""
    mujoco = _mj()
    data = mujoco.MjData(model)
    c = _coupling_si(comp, plan, joints)
    drivers = plan.drivers
    k_ind = _si(comp, drivers)
    di = [plan.joints.index(d) for d in drivers]
    q_plan = plan.q[:, di] * k_ind
    qd_plan = plan.qd[:, di] * k_ind
    ff_ind = ff @ c                                   # τ_незав = Cᵀ·τ_все (строками)
    lim = np.array([comp.mechanism.links[d].joint.drive.limit for d in drivers])
    w = 2.0 * math.pi * SERVO_HZ
    n = len(joints)
    q = q_plan[0].copy()
    v = np.zeros(len(drivers))
    full = np.zeros((model.nv, model.nv))
    step = model.opt.timestep
    track = np.zeros((len(plan.t), len(drivers)))
    applied = np.zeros((len(plan.t), len(drivers)))
    t = 0.0
    u = np.zeros(len(drivers))
    for i, ti in enumerate(plan.t):
        while t < ti - 1e-12:
            data.qpos[:n] = c @ q
            data.qvel[:n] = c @ v
            mujoco.mj_forward(model, data)
            mujoco.mj_fullM(model, data, full)
            m_r = c.T @ full[:n, :n] @ c
            h_r = c.T @ (data.qfrc_bias[:n] - data.qfrc_passive[:n])
            qr = np.array([np.interp(t, plan.t, q_plan[:, j]) for j in range(len(drivers))])
            vr = np.array([np.interp(t, plan.t, qd_plan[:, j]) for j in range(len(drivers))])
            fr = np.array([np.interp(t, plan.t, ff_ind[:, j]) for j in range(len(drivers))])
            u = np.clip(fr + m_r @ (w * w * (qr - q) + 2.0 * w * (vr - v)), -lim, lim)
            v = v + np.linalg.solve(m_r, u - h_r) * step
            q = q + v * step
            t += step
        track[i] = (q_plan[i] - q) / k_ind
        applied[i] = u
    return track, applied


def _frames(model, comp: Compiled, plan: Plan, joints: list[str]) -> dict:
    """Положения звеньев для проигрывателя окна (мм, кватернион w x y z)."""
    mujoco = _mj()
    data = mujoco.MjData(model)
    k = _si(comp, plan.joints)
    order = [plan.joints.index(j) for j in joints]
    idx = np.unique(np.linspace(0, len(plan.t) - 1, min(MAX_FRAMES, len(plan.t))).round().astype(int))
    names = list(comp.links)
    frames = []
    for i in idx:
        data.qpos[:len(joints)] = plan.q[i, order] * k[order]
        mujoco.mj_kinematics(model, data)
        frames.append([[*(data.xpos[model.body(n).id] * 1000.0).round(4).tolist(),
                        *data.xquat[model.body(n).id].round(7).tolist()] for n in names])
    return {"t": [round(float(plan.t[i]), 4) for i in idx], "links": names, "poses": frames,
            "q": [[round(float(v), 4) for v in plan.q[i]] for i in idx], "joints": plan.joints}


def link_matrix(model, data, name: str) -> np.ndarray:
    """4×4 звена в сцене, мм — по текущему состоянию `data`."""
    m = np.eye(4)
    b = model.body(name).id
    m[:3, :3] = data.xmat[b].reshape(3, 3)
    m[:3, 3] = data.xpos[b] * 1000.0
    return m


def support_polygon(comp: Compiled) -> tuple[np.ndarray, float]:
    """Опорный многоугольник основания: выпуклая оболочка того, чем оно стоит
    на столе (сечение 0.2 мм над самой нижней точкой), и высота стола, мм."""
    from shapely.geometry import MultiPoint

    from datum.geom.booleans import compose
    from datum.geom.measure import bbox, section_xy

    root = comp.links[comp.mechanism.root]
    whole = compose(root.solids)
    lo, _ = bbox(whole)
    z0 = float(lo[2])
    sec = section_xy(whole, z0 + 0.2)
    if sec.is_empty:
        raise MechanismError("основание не касается стола: сечение у низа пусто")
    hull = MultiPoint([pt for g in getattr(sec, "geoms", [sec]) for pt in g.exterior.coords]).convex_hull
    return np.asarray(hull.exterior.coords, float), z0


def _signed_distance(poly: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Расстояние от точек до границы выпуклого многоугольника: + внутри."""
    from shapely.geometry import Point, Polygon

    pg = Polygon(poly)
    out = np.empty(len(pts))
    for i, (x, y) in enumerate(pts):
        p = Point(x, y)
        d = pg.exterior.distance(p)
        out[i] = d if pg.contains(p) else -d
    return out


#: Наибольший сдвиг звена относительно соседа между двумя сверками задеваний, мм.
#: Прежде поз было 24 на ЛЮБОЙ сценарий, равномерно по времени: мах рычага на
#: 180° проверялся через десятки градусов, и рычаг «проходил» сквозь стенку
#: 2 мм — проверка отвечала «не задевают» (аудит 2026-11-21, воспроизведено).
#: Пропустить препятствие при шаге 1 мм можно, только если и оно, и идущее на
#: него тело вместе тоньше миллиметра вдоль хода.
CLEARANCE_STEP_MM = 1.0
#: Предел сверок на пару звеньев: длинный ход огрубляет шаг, и ответ это говорит.
CLEARANCE_MAX_PER_PAIR = 3000
#: Предел поз, в которых считается кинематика (она дёшева, но не бесплатна).
CLEARANCE_MAX_POSES = 40_000


def _corners(lo, hi) -> np.ndarray:
    """Восемь углов габарита — однородными координатами (8×4)."""
    xs = np.array([[x, y, z, 1.0] for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
                   for z in (lo[2], hi[2])])
    return xs


def clearance(comp: Compiled, model, plan: Plan, joints: list[str], *, samples: int = 24,
              tolerance_mm3: float = 0.5, info: dict | None = None) -> list[dict]:
    """Пересечения звеньев вдоль плана сверх нулевой позы — точной геометрией.

    Шаг — от геометрии, а не от времени: пара звеньев сверяется заново, когда
    одно сдвинулось относительно другого на `CLEARANCE_STEP_MM` (по углам
    габарита — это верхняя оценка сдвига любой точки при жёстком движении).
    Пара, которая друг относительно друга не движется, сверяется один раз.
    Между строками плана поза — линейной интерполяцией: при быстром ходе
    соседние строки (5 мс) дальше шага. `samples` — наименьшее число поз
    (совместимость с прежним вызовом). В `info` кладётся, каким вышел шаг и
    сколько было сверок: огрублённый шаг обязан попасть в ответ.
    """
    from datum.geom.booleans import compose
    from datum.geom.measure import bbox, vol
    from datum.geom.xform import place

    mujoco = _mj()
    data = mujoco.MjData(model)
    k = _si(comp, plan.joints)
    order = [plan.joints.index(j) for j in joints]
    names = list(comp.links)
    shapes = {n: compose(comp.links[n].solids) for n in names}
    pairs = [(a, b) for i, a in enumerate(names) for b in names[i + 1:]]
    boxes = {n: bbox(shapes[n]) for n in names}
    corners = {n: _corners(*boxes[n]) for n in names}

    def overlap(sa, sb) -> float:
        la, ha = bbox(sa)
        lb, hb = bbox(sb)
        if np.any(la > hb) or np.any(lb > ha):
            return 0.0
        return float(vol(sa ^ sb))

    def mats_at(f: float) -> dict[str, np.ndarray]:
        lo = int(math.floor(f))
        hi = min(lo + 1, len(plan.t) - 1)
        w = f - lo
        q = plan.q[lo] * (1.0 - w) + plan.q[hi] * w
        data.qpos[:len(joints)] = q[order] * k[order]
        mujoco.mj_kinematics(model, data)
        return {n: link_matrix(model, data, n) for n in names}

    def world(mats, n) -> np.ndarray:
        return (mats[n] @ corners[n].T).T[:, :3]

    base = {p: overlap(shapes[p[0]], shapes[p[1]]) for p in pairs}

    # 1. Позы: строки плана, а где звено между строками уходит дальше шага —
    #    ещё и промежуточные.
    last = len(plan.t) - 1
    rows = [mats_at(float(i)) for i in range(len(plan.t))]
    moved = np.zeros(max(last, 0))
    for i in range(last):
        moved[i] = max(float(np.abs(world(rows[i + 1], n) - world(rows[i], n)).max())
                       for n in names)
    pieces = np.maximum(1, np.ceil(moved / CLEARANCE_STEP_MM)).astype(int)
    if int(pieces.sum()) + 1 > CLEARANCE_MAX_POSES:
        scale = (CLEARANCE_MAX_POSES - 1) / float(pieces.sum())
        pieces = np.maximum(1, np.floor(pieces * scale)).astype(int)
    fracs: list[float] = []
    for i in range(last):
        fracs.extend(i + j / pieces[i] for j in range(int(pieces[i])))
    fracs.append(float(last))
    must = {int(np.argmin(np.abs(plan.t - tp))) for tp in plan.pose_t} | {0, last} \
        | set(np.linspace(0, last, max(int(samples), 2)).round().astype(int).tolist())
    poses = [rows[int(f)] if float(f).is_integer() else mats_at(f) for f in fracs]

    # 2. Для каждой пары — путь одного звена относительно другого и шаг сверки.
    worst: dict[tuple[str, str], dict] = {}
    checks, coarsest = 0, 0.0
    for a, b in pairs:
        # Сдвиг — в ОБЕ стороны: углы A в рамке B и углы B в рамке A. Одних
        # углов A мало: короткая стойка у оси почти не движется в рамке
        # длинного рычага, а его дальний конец проходит мимо неё сотни мм.
        rel = [np.vstack([(np.linalg.inv(m[b]) @ m[a] @ corners[a].T).T[:, :3],
                          (np.linalg.inv(m[a]) @ m[b] @ corners[b].T).T[:, :3]])
               for m in poses]
        hops = [float(np.abs(rel[i + 1] - rel[i]).max()) for i in range(len(rel) - 1)]
        path = float(sum(hops))
        step = max(CLEARANCE_STEP_MM, path / CLEARANCE_MAX_PER_PAIR)
        if path > 1e-9:
            coarsest = max(coarsest, step)
        seen = None
        for idx, f in enumerate(fracs):
            due = (seen is None or (float(f).is_integer() and int(f) in must and path > 1e-9)
                   or float(np.abs(rel[idx] - seen).max()) >= step)
            if not due:
                continue
            seen = rel[idx]
            # Грубая отсечка по габаритам в сцене — до булевой.
            wa, wb = world(poses[idx], a), world(poses[idx], b)
            if np.any(wa.min(0) > wb.max(0)) or np.any(wb.min(0) > wa.max(0)):
                continue
            checks += 1
            # Положенные тела не копятся: по сетке на каждую позу — это
            # гигабайты на тяжёлом звене (рука-v3 упёрлась в предел памяти).
            v = overlap(place(shapes[a], poses[idx][a]),
                        place(shapes[b], poses[idx][b])) - base[(a, b)]
            if v > tolerance_mm3 and v > worst.get((a, b), {}).get("volume_mm3", 0.0):
                lo = int(math.floor(f))
                hi = min(lo + 1, last)
                w = f - lo
                q = plan.q[lo] * (1.0 - w) + plan.q[hi] * w
                t = float(plan.t[lo] * (1.0 - w) + plan.t[hi] * w)
                worst[(a, b)] = {"a": a, "b": b, "volume_mm3": round(v, 3),
                                 "t": round(t, 3),
                                 "q": {j: round(float(q[plan.joints.index(j)]), 3)
                                       for j in plan.joints}}
    if info is not None:
        info.update({"step_mm": round(coarsest or CLEARANCE_STEP_MM, 3),
                     "poses": len(fracs), "checks": checks})
    return sorted(worst.values(), key=lambda d: -d["volume_mm3"])


def _drive_loads(comp: Compiled, plan: Plan, required_all: np.ndarray, react: np.ndarray):
    """Что должен дать ПРИВОД каждого независимого сустава — с трением и КПД.

    Трение опоры — по реакциям: τ = drag + μ·F_рад·r + μ_ос·F_ос·r_ос, против
    движения; в покое оно ПОМОГАЕТ держать (кинетический μ — осторожно: покоя
    он не больше). Ведомый (шестерня) нагружает ведущего через отношение и КПД
    зацепления: поглощает мощность — ведущий даёт c/η, отдаёт — c·η.

    Привод (паспортный предел — на ВЫХОДЕ, за вычетом прямых потерь): ведёт груз
    — нужно |τ|; груз ведёт его — |τ|·|η_обр|·η (самотормозящий червяк при этом
    толкает, а не тормозит); стоит — самотормозящая передача держит сама (0),
    обратимая — |τ|·η_обр·η.

    Возвращает (требуемое приводу со знаком (n, D), жёсткое (n, D), трение (n, J))."""
    mech = comp.mechanism
    joints, drivers = plan.joints, plan.drivers
    c = _coupling_si(comp, plan, joints)
    n = len(plan.t)
    fr = np.zeros((n, len(joints)))
    for k, name in enumerate(joints):
        b = mech.links[name].joint.friction
        if b is None:
            continue
        if mech.links[name].joint.kind == "prismatic":
            # Ползун: трение — СИЛА μ·N от поперечной нагрузки направляющей, без
            # плеча. Формула шарнира (μ·F·r) давала у ползуна 1 кг при μ = 0.3
            # ноль ньютонов вместо 2.94 (аудит 2026-11-21): радиуса у
            # направляющей нет. Осевая реакция ползуна — сама движущая сила,
            # трения она не создаёт.
            fr[:, k] = b.drag + b.mu * react[:, k, 0]
        else:
            fr[:, k] = (b.drag + b.mu * react[:, k, 0] * b.radius_mm / 1000.0
                        + b.mu_axial * react[:, k, 1] * b.axial_radius_mm / 1000.0)
    w = plan.qd                                     # знак скорости — в своих единицах
    rigid = required_all @ c
    need = np.zeros((n, len(drivers)))
    for di, d in enumerate(drivers):
        kd = joints.index(d)
        moving = np.abs(w[:, kd]) > 1e-9
        sgn = np.sign(w[:, kd])
        own = required_all[:, kd] + sgn * fr[:, kd]
        total = own.copy()
        hold_rigid = required_all[:, kd].copy()
        hold_fric = fr[:, kd].copy()
        for k, name in enumerate(joints):
            j = mech.links[name].joint
            if j.follows is None or j.follows[0] != d:
                continue
            ck = c[k, di] * (required_all[:, k] + np.sign(w[:, k]) * fr[:, k])
            eta = j.mesh_efficiency
            absorbs = ck * sgn >= 0
            total += np.where(absorbs, ck / eta, ck * eta)
            hold_rigid += c[k, di] * required_all[:, k]
            hold_fric += abs(c[k, di]) * fr[:, k]
        held = np.sign(hold_rigid) * np.maximum(0.0, np.abs(hold_rigid) - hold_fric)
        tau = np.where(moving, total, held)
        drv = mech.links[d].joint.drive
        if drv is not None:
            power = tau * w[:, kd]
            back = abs(drv.back) * drv.efficiency
            mag = np.abs(tau)
            eff = np.where(moving & (power >= 0), mag,
                           np.where(moving, mag * back,
                                    0.0 if drv.self_locking else mag * back))
            tau = np.sign(tau) * eff
        need[:, di] = tau
    return need, rigid, fr


def _utilization(comp: Compiled, plan: Plan, need: np.ndarray, react: np.ndarray,
                 tip: np.ndarray | None, poly) -> dict:
    """Загрузка каждого звена во времени: 0 — свободно, 1 — на пределе.

    Звено с суставом — наибольшее из: привод (нужно / предел) и опора сустава
    (радиальная, осевая сила, момент поперёк оси против `capacity`). Ведомое
    звено берёт загрузку своего ведущего привода. Основание: стоящее — насколько
    центр давления ушёл от центра опоры к краю, закреплённое — 0 (что держит
    крепление — проверка sim_base)."""
    mech = comp.mechanism
    out: dict[str, np.ndarray] = {}
    for name, lk in mech.links.items():
        j = lk.joint
        if j is None:
            u = np.zeros(len(plan.t))
            if mech.base == "resting" and tip is not None and poly is not None:
                from shapely.geometry import Polygon

                inner = Polygon(poly)
                c0 = inner.centroid
                reach = inner.exterior.distance(c0)
                u = np.clip(1.0 - np.where(np.isfinite(tip), tip, -reach) / max(reach, 1e-9), 0, None)
            out[name] = u
            continue
        leader = j.follows[0] if j.follows else name
        u = np.zeros(len(plan.t))
        dj = mech.links[leader].joint
        if leader in plan.drivers and dj.drive is not None:
            u = np.abs(need[:, plan.drivers.index(leader)]) / dj.drive.limit
        cap = j.capacity
        if cap is not None:
            k = plan.joints.index(name)
            for col, lim in ((0, cap.radial), (1, cap.axial), (2, cap.moment)):
                if lim > 0:
                    u = np.maximum(u, react[:, k, col] / lim)
        out[name] = u
    return out


def run_scenario(comp: Compiled, scenario: str, *, forward: bool = True,
                 geometry: bool = True, samples: int = 24) -> SimResult:
    from datum.sim.model import build_model

    mech = comp.mechanism
    if scenario not in mech.scenarios:
        raise MechanismError(f"нет сценария {scenario!r}; есть: {sorted(mech.scenarios) or 'никаких'}")
    sc = mech.scenarios[scenario]
    plan = plan_motion(comp, sc)
    notes = list(plan.notes)
    model, joints = build_model(comp, payloads=sc.payloads)
    required_all, _, react = _inverse(model, comp, plan, joints, free=False)
    order = [joints.index(j) for j in plan.joints]
    required_all, react = required_all[:, order], react[:, order]
    # Ведомые (шестерня за сердцем) нагрузку передают ведущему через отношение и
    # КПД зацепления; трение опор — по реакциям; привод — с КПД и самоторможением.
    required, required_rigid, friction = _drive_loads(comp, plan, required_all, react)
    ideal = [d for d in plan.drivers if mech.links[d].joint.drive is not None
             and mech.links[d].joint.drive.efficiency == 1.0
             and mech.links[d].joint.drive.back == 1.0]
    if not any(mech.links[j].joint.friction for j in plan.joints):
        notes.append("трение опор не задано (bearing) — запас оптимистичен")
    if ideal:
        notes.append(f"КПД привода не задан ({', '.join(ideal)}) — передача идеальная, запас "
                     f"оптимистичен")
    free_model, fj = build_model(comp, payloads=sc.payloads, free_base=True)
    _, wrench = _inverse(free_model, comp, plan, fj, free=True)
    force, moment = wrench[:, :3], wrench[:, 3:]
    cop = tip = poly = slip = None
    if mech.base == "resting":
        poly, z0 = support_polygon(comp)
        fz = force[:, 2]
        z = z0 / 1000.0
        with np.errstate(divide="ignore", invalid="ignore"):
            px = (z * force[:, 0] - moment[:, 1]) / fz
            py = (moment[:, 0] + z * force[:, 1]) / fz
        cop = np.column_stack([px, py]) * 1000.0
        tip = _signed_distance(poly, cop)
        tip[fz <= 0] = -np.inf                       # опора тянет вниз — основание отрывается
        slip = np.hypot(force[:, 0], force[:, 1]) / np.maximum(mech.friction * fz, 1e-12)
    tracking = applied = None
    undriven = [j for j in plan.drivers if mech.links[j].joint.drive is None]
    if forward and not undriven:
        ff = required_all[:, [plan.joints.index(j) for j in joints]]
        if len(plan.drivers) == len(plan.joints):
            tracking, applied = _forward(model, comp, plan, joints, ff)
        else:
            tracking, applied = _forward_reduced(model, comp, plan, joints, ff)
    elif forward:
        notes.append("прямого прогона нет: не у всех суставов есть привод "
                     f"({', '.join(undriven)}) — без привода звено падало бы")
    swept: dict = {}
    hits = clearance(comp, model, plan, joints, samples=samples, info=swept) if geometry else []
    if swept.get("step_mm", 0.0) > CLEARANCE_STEP_MM + 1e-9:
        notes.append(f"задевания: ход длинный, шаг сверки огрублён до "
                     f"{swept['step_mm']:g} мм (обычно {CLEARANCE_STEP_MM:g}) — препятствие "
                     f"тоньше этого могло быть пропущено; разбейте сценарий на части")
    frames = _frames(model, comp, plan, joints)
    lash = _backlash(comp, plan, required, model, joints, sc.payloads)
    util = _utilization(comp, plan, required, react, tip, poly)
    return SimResult(scenario, plan, required, tracking, applied, force, moment, cop, tip, poly,
                     slip, hits, frames, notes, required_rigid=required_rigid, reactions=react,
                     friction=friction, utilization=util, backlash=lash, clearance_info=swept)


def _backlash(comp: Compiled, plan: Plan, need: np.ndarray, model, joints: list[str],
              payloads) -> dict:
    """Люфт: где момент меняет знак — звено перескакивает зазор (стук, рывок), и
    сколько хода теряется в точках грузов (Σ люфт сустава × плечо до его оси)."""
    mujoco = _mj()
    mech = comp.mechanism
    events, lost = [], {}
    lashed = [d for d in plan.drivers
              if mech.links[d].joint.drive is not None and mech.links[d].joint.drive.backlash > 0]
    for d in lashed:
        tau = need[:, plan.drivers.index(d)]
        # Порог «момент есть» — от САМОГО момента сустава (1 % пика), а не от
        # предела привода: при моменте в восемь раз меньше предела перескок
        # засчитывался на 0.15 с позже нуля (поймано тестом рычага).
        floor = 0.01 * float(np.max(np.abs(tau))) if tau.size else 0.0
        sig = np.where(np.abs(tau) > floor, np.sign(tau), 0.0)
        last = 0.0
        for i, v in enumerate(sig):
            if v == 0:
                continue
            if last != 0 and v != last:
                events.append({"joint": d, "t": round(float(plan.t[i]), 3)})
            last = v
    if lashed and payloads:
        data = mujoco.MjData(model)
        k = _si(comp, plan.joints)
        order = [plan.joints.index(j) for j in joints]
        for p in payloads:
            chain = [n for n in mech.chain(mech.payloads[p].link) if n in lashed]
            worst = 0.0
            for i in range(0, len(plan.t), max(1, len(plan.t) // 200)):
                data.qpos[:len(joints)] = plan.q[i, order] * k[order]
                mujoco.mj_kinematics(model, data)
                pt = data.xpos[model.body(f"груз {p}").id]
                total = 0.0
                for n in chain:
                    j = model.joint(n).id
                    b = mech.links[n].joint.drive.backlash
                    if mech.links[n].joint.kind == "revolute":
                        arm = np.linalg.norm(np.cross(data.xaxis[j], pt - data.xanchor[j]))
                        total += math.radians(b) * arm * 1000.0
                    else:
                        total += b
                worst = max(worst, total)
            lost[p] = round(worst, 3)
    return {"reversals": events, "lost_motion_mm": lost, "joints": lashed}


def poses_at(comp: Compiled, plan: Plan, indices) -> list[dict[str, np.ndarray]]:
    """Матрицы звеньев (мм) в отсчётах плана `indices`."""
    from datum.sim.model import build_model

    mujoco = _mj()
    model, joints = build_model(comp)
    data = mujoco.MjData(model)
    k = _si(comp, plan.joints)
    order = [plan.joints.index(j) for j in joints]
    out = []
    for i in indices:
        data.qpos[:len(joints)] = plan.q[int(i), order] * k[order]
        mujoco.mj_kinematics(model, data)
        out.append({n: link_matrix(model, data, n) for n in comp.links})
    return out
