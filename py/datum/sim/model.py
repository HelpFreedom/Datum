"""Модель MuJoCo из механизма — ЕДИНСТВЕННОЕ место, где импортируется mujoco.

Как и manifold3d только в `geom`, так mujoco только здесь (держит
`tests/unit/test_layering.py`): версия ядра физики меняет числа, и знать о
ней должен один модуль.

Устройство модели:
  * рамки всех тел совпадают со сценой при нулевых суставах: тело звена
    стоит в нуле родителя, а ось сустава и центр масс даны прямо в сцене.
    Так нет ни одного пересчёта систем координат, на котором можно ошибиться
    знаком;
  * масса и инерция — явные (`explicitinertial`), из `sim.compile`: MuJoCo не
    считает их по сетке сам — сетку он видит выпуклой оболочкой (замерено:
    шар в пустоте П-образной детали «касается» её);
  * контакты ВЫКЛЮЧЕНЫ. Задевания деталей по траектории проверяет точная
    геометрия Datum (`sim.run.clearance`), опрокидывание — опорный
    многоугольник по реакции основания (`sim.run.base_load`). Контакт по
    выпуклым оболочкам дал бы правдоподобное, но неверное число;
  * якобиан — ПЛОТНЫЙ: в MuJoCo 4.0.0 известна ошибка разреженного разложения
    Холецкого (issue #3767, исправлена после выпуска), а наши модели малы;
  * интегратор implicitfast, шаг 1 мс.
"""

from __future__ import annotations

import math

import numpy as np

from datum.sim.compile import Compiled
from datum.sim.mechanism import MechanismError

__all__ = ["build_model", "write_mjcf", "check_urdf", "MJ_VERSION", "TIMESTEP_S", "GRAVITY"]

TIMESTEP_S = 0.001
GRAVITY = 9.81


def _mj():
    try:
        import mujoco
    except ImportError as e:                   # pragma: no cover — ставится с extra «sim»
        raise MechanismError("MuJoCo не установлен: pip install 'datum[sim]' "
                             "(пакет mujoco-core)") from e
    return mujoco


def MJ_VERSION() -> str:                       # noqa: N802 — читается как константа
    return _mj().__version__


def _fullinertia(i: np.ndarray) -> list[float]:
    """Тензор → [ixx, iyy, izz, ixy, ixz, iyz] (порядок fullinertia MuJoCo).

    Положительную определённость обеспечивает сложение тел; у звена из одной
    тонкой пластины главный момент может выйти на уровне ошибки округления —
    добавляем 1e-12 кг·м² по диагонали, чтобы компилятор не отказал."""
    i = 0.5 * (np.asarray(i, float) + np.asarray(i, float).T)
    i = i + np.eye(3) * 1e-12
    return [i[0, 0], i[1, 1], i[2, 2], i[0, 1], i[0, 2], i[1, 2]]


def build_model(comp: Compiled, *, payloads: tuple[str, ...] = (), free_base: bool = False):
    """MjModel механизма. `free_base` — основание на свободном суставе (для
    реакции опоры обратной динамикой); иначе приварено к миру.

    Связи ведомых суставов в модели НЕТ: её учитывает матрица C
    (`Mechanism.coupling`) — в обратной динамике и в прямом прогоне в
    независимых координатах (`sim.run._forward_reduced`). Мягкое равенство
    MuJoCo разгоняло лёгкий ведущий вал раньше, чем подтягивало ведомый
    (ведущий уходил на сотни градусов), и ответ зависел бы от его настройки.

    Возвращает (model, joint_names): суставы в порядке qpos (после 7 чисел
    свободного основания, если оно есть)."""
    spec, joint_names, _ = _spec(comp, payloads=payloads, free_base=free_base)
    return spec.compile(), joint_names


def _spec(comp: Compiled, *, payloads: tuple[str, ...] = (), free_base: bool = False):
    """MjSpec механизма до компиляции: (spec, суставы, тела по именам звеньев)."""
    mujoco = _mj()
    mech = comp.mechanism
    spec = mujoco.MjSpec()
    spec.modelname = "datum"
    # Углы — в РАДИАНАХ. По умолчанию MjSpec, как MJCF, читает их в градусах:
    # ход ±2π рад становился ±6.28°, и упор сустава давал «момент» 3.4 Н·м
    # там, где диску нужно 0.00025 (поймано тестом разгона диска).
    spec.compiler.degree = False
    spec.option.timestep = TIMESTEP_S
    spec.option.gravity = [0.0, 0.0, -GRAVITY]
    spec.option.integrator = mujoco.mjtIntegrator.mjINT_IMPLICITFAST
    spec.option.jacobian = mujoco.mjtJacobian.mjJAC_DENSE
    spec.option.disableflags |= int(mujoco.mjtDisableBit.mjDSBL_CONTACT)

    bodies = {}
    joint_names: list[str] = []
    for name, lk in comp.links.items():
        parent = spec.worldbody if lk.parent is None else bodies[lk.parent]
        body = parent.add_body(name=name, pos=[0.0, 0.0, 0.0])
        body.explicitinertial = True
        body.mass = float(lk.mass_kg)
        body.ipos = [float(v) for v in lk.com_m]
        body.fullinertia = _fullinertia(lk.inertia_kgm2)
        if lk.parent is None and free_base:
            body.add_freejoint(name="основание")
        j = lk.joint
        if j is not None:
            if j.kind == "revolute":
                jt = body.add_joint(name=name, type=mujoco.mjtJoint.mjJNT_HINGE,
                                    axis=list(j.axis), pos=[v / 1000.0 for v in j.origin])
                jt.range = [math.radians(j.limits[0]), math.radians(j.limits[1])]
            else:
                jt = body.add_joint(name=name, type=mujoco.mjtJoint.mjJNT_SLIDE,
                                    axis=list(j.axis), pos=[0.0, 0.0, 0.0])
                jt.range = [j.limits[0] / 1000.0, j.limits[1] / 1000.0]
            jt.limited = mujoco.mjtLimited.mjLIMITED_TRUE
            if j.drive is not None and j.drive.rotor_inertia > 0:
                # инерция ротора, приведённая к выходу: I_рот·i² (кг·м²; ползун — кг)
                jt.armature = float(j.drive.rotor_inertia)
            joint_names.append(name)
        bodies[name] = body

    for pname in payloads:
        p = mech.payloads[pname]
        b = bodies[p.link].add_body(name=f"груз {pname}", pos=[v / 1000.0 for v in p.at])
        b.explicitinertial = True
        b.mass = p.mass_g / 1000.0
        b.ipos = [0.0, 0.0, 0.0]
        # точечный груз: ничтожная собственная инерция (компилятор не берёт ноль)
        b.fullinertia = [1e-9, 1e-9, 1e-9, 0.0, 0.0, 0.0]

    # Приводы — МОТОРЫ: команда — момент (сила), обрезанный пределом привода.
    # Регулятор — в `sim.run._forward` (упреждение по обратной динамике плюс
    # поправка по ошибке): так отставание появляется только там, где момента
    # не хватает, а не от настройки регулятора.
    for name in joint_names:
        d = mech.links[name].joint.drive                    # type: ignore[union-attr]
        if d is None:
            continue
        act = spec.add_actuator(name=name, target=name, trntype=mujoco.mjtTrn.mjTRN_JOINT)
        act.set_to_motor()
        act.ctrlrange = [-d.limit, d.limit]
        act.ctrllimited = mujoco.mjtLimited.mjLIMITED_TRUE

    return spec, joint_names, bodies


# ================================================================== наружу
#: Сколько случайных поз сверяется при перечитывании записанной модели.
VERIFY_POSES = 24
#: Допуск сверки центров масс, м, и массы (доля). MuJoCo пишет в XML ШЕСТЬ
#: значащих цифр (замерено: масса 0.39993607 → 0.399936, центр масс уходит на
#: 0.5 мкм) — точнее файл быть не может. Ошибка, ради которой сверка есть
#: (не та рамка, градусы вместо радиан, потерянное звено), — миллиметры.
VERIFY_TOL_M = 1e-5
VERIFY_TOL_MASS = 1e-5


def _worst_com(ref, ref_joints: list[str], other) -> float:
    """Наибольшее расхождение центров масс звеньев двух моделей в случайных
    позах, м. Тела сопоставляются по именам; не нашлось — отказ."""
    mujoco = _mj()
    rng = np.random.default_rng(20261122)
    d0, d1 = mujoco.MjData(ref), mujoco.MjData(other)
    names0 = {ref.body(i).name: i for i in range(1, ref.nbody)}
    names1 = {other.body(i).name: i for i in range(1, other.nbody)}
    if set(names0) != set(names1):
        raise MechanismError(f"в записанной модели тела {sorted(names1)}, а в расчётной "
                             f"{sorted(names0)}")
    joints1 = {other.joint(i).name: int(other.jnt_qposadr[i]) for i in range(other.njnt)}
    if set(joints1) != set(ref_joints):
        raise MechanismError(f"в записанной модели суставы {sorted(joints1)}, а в расчётной "
                             f"{sorted(ref_joints)}")
    mass0 = float(ref.body_mass.sum())
    if abs(float(other.body_mass.sum()) - mass0) > VERIFY_TOL_MASS * mass0:
        raise MechanismError(f"масса записанной модели {float(other.body_mass.sum()):.9g} кг, "
                             f"расчётной {mass0:.9g}")
    worst = 0.0
    lo, hi = ref.jnt_range[:, 0], ref.jnt_range[:, 1]
    for _ in range(VERIFY_POSES):
        # Ход бывает в сотни оборотов (сердце Gear Cube) — поза берётся в
        # пределах одного оборота от нижней границы, чтобы не терять знаки.
        q = lo + rng.uniform(0.0, 1.0, len(lo)) * np.minimum(hi - lo, 2 * math.pi)
        d0.qpos[:len(ref_joints)] = q
        for k, name in enumerate(ref_joints):
            d1.qpos[joints1[name]] = q[k]
        mujoco.mj_kinematics(ref, d0)
        mujoco.mj_comPos(ref, d0)
        mujoco.mj_kinematics(other, d1)
        mujoco.mj_comPos(other, d1)
        for name, i in names0.items():
            worst = max(worst, float(np.abs(d0.xipos[i] - d1.xipos[names1[name]]).max()))
    if worst > VERIFY_TOL_M:
        raise MechanismError(f"записанная модель расходится с расчётной: центры масс до "
                             f"{worst * 1000:.4f} мм — файл не годится")
    return worst


def write_mjcf(comp: Compiled, path, *, name: str, meshes: dict[str, str],
               colours: dict[str, str] | None = None,
               payloads: tuple[str, ...] = ()) -> dict:
    """Записать MJCF механизма в `path` и сверить, перечитав.

    Это ТА ЖЕ модель, что считает симуляция (`_spec`), плюс то, что нужно
    чужому симулятору: сетки звеньев (`meshes`: звено → имя STL в каталоге
    `meshes/` рядом, миллиметры) — только видом, без контактов, — и связи
    ведомых суставов равенством `joint` (в расчёте Datum их заменяет матрица
    C; чужой программе о передаче иначе не узнать)."""
    from pathlib import Path

    mujoco = _mj()
    path = Path(path)
    mech = comp.mechanism
    spec, joint_names, bodies = _spec(comp, payloads=payloads)
    spec.modelname = name
    spec.modelfiledir = str(path.parent)
    spec.meshdir = "meshes"
    for link, file in meshes.items():
        spec.add_mesh(name=link, file=file, scale=[0.001, 0.001, 0.001])
        rgba = [0.6, 0.64, 0.7, 1.0]
        if colours and colours.get(link):
            c = colours[link].lstrip("#")
            rgba = [int(c[i:i + 2], 16) / 255 for i in (0, 2, 4)] + [1.0]
        bodies[link].add_geom(type=mujoco.mjtGeom.mjGEOM_MESH, meshname=link, contype=0,
                              conaffinity=0, group=2, rgba=rgba)
    couplings = 0
    for link in joint_names:
        j = mech.links[link].joint
        if j is None or j.follows is None:
            continue
        master, ratio = j.follows
        si = math.pi / 180.0 if j.kind == "revolute" else 1e-3
        m_si = math.pi / 180.0 if mech.links[master].joint.kind == "revolute" else 1e-3
        eq = spec.add_equality(name=f"{link} за {master}", type=mujoco.mjtEq.mjEQ_JOINT,
                               name1=link, name2=master, objtype=mujoco.mjtObj.mjOBJ_JOINT)
        eq.data[:5] = [0.0, ratio * si / m_si, 0.0, 0.0, 0.0]
        couplings += 1
    try:
        spec.compile()
        text = spec.to_xml()
    except Exception as e:                       # noqa: BLE001 — MuJoCo бросает своё
        raise MechanismError(f"MuJoCo не собрал модель для записи: {e}") from e
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        ref, ref_joints = build_model(comp, payloads=payloads)
        back = mujoco.MjModel.from_xml_path(str(tmp))
        worst = _worst_com(ref, ref_joints, back)
        if back.nmesh != len(meshes):
            raise MechanismError(f"в записанной модели сеток {back.nmesh}, а звеньев с "
                                 f"сеткой {len(meshes)}")
    except MechanismError:
        tmp.unlink(missing_ok=True)
        raise
    except Exception as e:                       # noqa: BLE001
        tmp.unlink(missing_ok=True)
        raise MechanismError(f"записанный MJCF не читается MuJoCo: {e}") from e
    tmp.replace(path)
    return {"bodies": int(back.nbody - 1), "joints": int(back.njnt),
            "actuators": int(back.nu), "couplings": couplings, "worst_m": worst,
            "mass_kg": float(back.body_mass.sum())}


def check_urdf(comp: Compiled, path, *, payloads: tuple[str, ...] = ()) -> float:
    """Перечитать URDF загрузчиком MuJoCo и сверить с расчётной моделью:
    наибольшее расхождение центров масс, м. Неподвижные тела НЕ сливаются
    (иначе основание уходит в мир, а груз — в своё звено, и сверять нечего);
    сетки вида при этом не читаются — сверяются кинематика и массы."""
    mujoco = _mj()
    try:
        spec = mujoco.MjSpec.from_file(str(path))
        spec.compiler.fusestatic = False
        back = spec.compile()
    except Exception as e:                       # noqa: BLE001
        raise MechanismError(f"записанный URDF не читается MuJoCo: {e}") from e
    ref, ref_joints = build_model(comp, payloads=payloads)
    return _worst_com(ref, ref_joints, back)
