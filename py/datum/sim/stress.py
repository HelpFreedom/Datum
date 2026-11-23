"""Напряжения в печатном звене в ХУДШИЙ момент прогона — МКЭ по нагрузкам MuJoCo.

Карта загрузки говорит, насколько звено близко к пределу ПРИВОДА и опоры;
выдержит ли сама печатная деталь — она не говорит. Здесь звено считается как
упругое тело в тот момент сценария, когда изгибающий момент в его суставе
наибольший:

  * худший момент — пик |M| реакции сустава звена у его оси (изгиб и кручение
    у корня звена — то, что ломает печатную деталь), обратной динамикой по
    плану (`mj_inverse` + `mj_rnePostConstraint`);
  * несущее тело — печатные детали звена, касающиеся друг друга (зазор не
    больше `BOND_GAP_MM`), группой, которая подходит к оси сустава: детали,
    стянутые винтами, считаются СКЛЕЕННЫМИ (это оптимистично у самих стыков —
    винтов и трения в модели нет, и говорится в ответе);
  * опора — поверхность, которой звено КАСАЕТСЯ родителя у оси сустава (вал в
    отверстии, подшипник в гнезде, торец о щёку), защемлена; касания нет —
    поверхность, ближайшая к оси (и ответ это говорит). Куски сетки, разделённые
    зазором меньше `BOND_GAP_MM`, склеены пружинами (`fem.loads.tie_pairs`);
  * нагрузки — в системе звена (поза ноль): сила и момент от каждого
    следующего звена у ЕГО оси (через касание с ним), от грузов — у их точки, от
    тел звена вне несущей группы (мотор, подшипник, крышка) — их вес и силы
    инерции в их центре масс, через поверхность касания с ними; вес и
    силы инерции самого несущего тела — объёмной силой по полю ускорений
    жёсткого тела (вес — вместе с ускорением: MuJoCo отдаёт «a − g»);
  * материал — изотропный (модуль MAT_E проекта; печать анизотропна), предел —
    прочность МЕЖДУ СЛОЯМИ (MAT_TENSILE_Z): слабейшая, место и направление
    растяжения относительно слоёв модель не знает. Вдоль слоёв — для
    сравнения;
  * две сетки (ребро и вдвое крупнее, строятся разом в разных процессах): с
    пределом сравнивается БОЛЬШИЙ из их пиков вне зоны опоры и пятен нагрузки —
    там напряжение зависит от способа приложения (Сен-Венан), а мешер не
    детерминирован, и оценка сверху честнее удачной сетки.

Сверка: сумма приложенного обязана уравновесить реакцию родителя на звено из
MuJoCo; расхождение (центр масс печати не совпадает с центром объёма, инерция
вращения навесных тел не учтена) — в ответе процентом.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from datum.sim.compile import Compiled
from datum.sim.mechanism import MechanismError

__all__ = ["StressReport", "link_stress", "stress_candidates", "worst_instant",
           "why_no_candidates", "BOND_GAP_MM", "STRUCT_REACH_MM"]

#: Детали звена, между которыми не больше этого, — одно несущее тело (склеены).
BOND_GAP_MM = 0.05
#: Несущая группа — та, что подходит к оси сустава не дальше ближайшей + столько.
STRUCT_REACH_MM = 10.0
#: Опора и пятна у осей: поверхность в пределах ближайшей к оси + столько.
AXIS_BAND_MM = 1.0
#: Пятно у точки (груз, навесное тело): ближайшая поверхность + столько.
POINT_BAND_MM = 3.0
#: Касание: центр грани не дальше этого (плюс конверт мешера) от поверхности соседа —
#: посадка с зазором — или внутри него не глубже CONTACT_INSIDE_MM (с натягом).
CONTACT_GAPS_MM = (0.6,)
CONTACT_INSIDE_MM = 3.0
#: Шаг представителей поверхности соседа (`geom.proximity`): ошибка расстояния ≤ 0.07 мм при 1.
CONTACT_SPACING_MM = 0.5
#: Опора у оси — касания не дальше этого от оси и не дальше ближайшего касания + запас
#: (у плеча руки вал J2 — в отверстии 8.5 мм, подшипник другой щеки — 15.5 мм).
AXIS_CONTACT_MAX_MM = 80.0
AXIS_CONTACT_REACH_MM = 15.0
#: Зона опоры, в которой пик не судится (не меньше, мм; и не меньше 1.5 ребра сетки):
#: у защемлённой поверхности напряжение растёт с измельчением без предела.
SUPPORT_ZONE_MM = 3.0
#: Сколько раз строить сетку заново, если на ней не прошёл тест заплаты (мешер случаен).
MESH_ATTEMPTS = 3
#: Сколько отсчётов плана перебирается в поиске худшего момента.
SCAN_SAMPLES = 800
#: Сошлось — пик между сетками сдвинулся меньше; особенность — вырос больше.
CONVERGED = 0.10
SINGULAR_GROWTH = 0.20


@dataclass
class StressReport:
    link: str
    t: float
    index: int
    pieces: list[str]
    loose: list[str]
    value_mpa: float                   # то, что сравнивается с пределом
    basis: str                         # «пик» | «99-й процентиль» | «пик, не сошлось»
    peak_mpa: float
    peak_coarse_mpa: float
    p99_mpa: float
    p99_coarse_mpa: float
    support_peak_mpa: float            # пик у опоры и мест приложения нагрузки (не судится)
    peak_at: tuple[float, float, float]
    max_displacement_mm: float
    strength_z: float | None
    strength_xy: float | None
    imbalance: tuple[float, float]
    mujoco_mismatch: float
    loads: list[dict]
    mesh: dict
    notes: list[str] = field(default_factory=list)
    # для карты: треугольники поверхности (мм, система звена) и Мизес в вершинах
    verts: np.ndarray | None = field(default=None, repr=False)
    faces: np.ndarray | None = field(default=None, repr=False)
    node_vm: np.ndarray | None = field(default=None, repr=False)

    @property
    def utilization(self) -> float | None:
        return None if not self.strength_z else self.value_mpa / self.strength_z

    def as_dict(self) -> dict:
        return {
            "link": self.link, "t": round(self.t, 4), "pieces": self.pieces, "loose": self.loose,
            "value_mpa": round(self.value_mpa, 3), "basis": self.basis,
            "peak_mpa": round(self.peak_mpa, 3), "peak_coarse_mpa": round(self.peak_coarse_mpa, 3),
            "p99_mpa": round(self.p99_mpa, 3), "p99_coarse_mpa": round(self.p99_coarse_mpa, 3),
            "support_peak_mpa": round(self.support_peak_mpa, 3),
            "peak_at": [round(v, 2) for v in self.peak_at],
            "max_displacement_mm": round(self.max_displacement_mm, 4),
            "strength_z_mpa": self.strength_z, "strength_xy_mpa": self.strength_xy,
            "utilization": None if self.utilization is None else round(self.utilization, 4),
            "equilibrium": [float(f"{v:.2e}") for v in self.imbalance],
            "mujoco_mismatch": round(self.mujoco_mismatch, 4),
            "loads": self.loads, "mesh": self.mesh, "notes": self.notes}


def _mj():
    from datum.sim.model import _mj as m

    return m()


def stress_candidates(comp: Compiled) -> list[str]:
    """Звенья, которые можно посчитать: шарнир (ось известна) и печатные тела."""
    out = []
    for name, lk in comp.links.items():
        if lk.joint is None or lk.joint.kind != "revolute":
            continue
        if any(p["part"] is not None for p in lk.pieces):
            out.append(name)
    return out


#: Ребро по толщине: столько рёбер на характерную толщину 2·V/A (у пластины — её
#: толщина). Доля диагонали для звена не годится: у плеча руки (диагональ 350 мм)
#: 0.03 давало 10 мм — один элемент на пластину 5 мм, и сетки расходились на 37 %.
EDGE_PER_THICKNESS = 0.8
#: Пределы ребра: мельче мешер работает минуты, крупнее — сетка не про деталь.
EDGE_RANGE = (0.004, 0.05)


def check_edge(edge: float) -> None:
    """0 — по толщине; иначе доля диагонали в пределах `EDGE_RANGE`…0.5. Мешер на
    ребре 1e-3 падал с bad_alloc, на 1e308 — с SIGSEGV (CLAUDE.md)."""
    lo = EDGE_RANGE[0]
    if edge != 0 and not lo <= edge <= 0.5:
        raise MechanismError(f"ребро сетки {edge:g} — долей диагонали от {lo:g} до 0.5, "
                             f"или 0: по толщине тела")


def mesh_edge(body) -> float:
    """Ребро сетки долей диагонали — по характерной толщине тела."""
    from datum.geom.measure import area, bbox, vol

    lo, hi = (np.asarray(v, float) for v in bbox(body))
    diag = float(np.linalg.norm(hi - lo))
    thick = 2.0 * vol(body) / max(area(body), 1e-9)
    return float(np.clip(EDGE_PER_THICKNESS * thick / max(diag, 1e-9), *EDGE_RANGE))


def why_no_candidates(comp: Compiled) -> str:
    """Почему считать нечего — словами, с тем, что поправить в объявлении механизма."""
    massed = [n for n, lk in comp.links.items()
              if lk.joint is not None and lk.joint.kind == "revolute"
              and lk.pieces and all(p["part"] is None for p in lk.pieces)]
    sliders = [n for n, lk in comp.links.items()
               if lk.joint is not None and lk.joint.kind != "revolute"]
    parts = ["считать нечего: напряжения считаются у звена на ШАРНИРЕ с печатными деталями"]
    if massed:
        parts.append(f"у звеньев {', '.join(massed)} все тела объявлены только массой "
                     f"(mass_g=…) — для МКЭ это покупное. Если тело печатное, в @mechanism "
                     f"укажите деталь: add(тело, part=«деталь», mass_g=…) — масса останется "
                     f"заданной (весы, G-code), а деталь посчитается")
    if sliders:
        parts.append(f"ползуны ({', '.join(sliders)}) пока не считаются: опора у них не определена")
    return "; ".join(parts)


def _axis_distance(points: np.ndarray, origin, axis) -> np.ndarray:
    a = np.asarray(axis, float)
    a = a / np.linalg.norm(a)
    r = np.asarray(points, float) - np.asarray(origin, float)
    return np.linalg.norm(np.cross(r, a), axis=-1)


def _state(model, data, comp: Compiled, plan, joints: list[str], i: int) -> None:
    from datum.sim.run import _si

    mujoco = _mj()
    k = _si(comp, plan.joints)
    order = [plan.joints.index(j) for j in joints]
    data.qpos[:len(joints)] = plan.q[i, order] * k[order]
    data.qvel[:len(joints)] = plan.qd[i, order] * k[order]
    data.qacc[:len(joints)] = plan.qdd[i, order] * k[order]
    mujoco.mj_inverse(model, data)
    mujoco.mj_rnePostConstraint(model, data)


def _wrench_at(model, data, body: int, point: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Сила родителя на тело и её момент о точке `point` (мир, СИ)."""
    f, t = data.cfrc_int[body][3:].copy(), data.cfrc_int[body][:3].copy()
    s = data.subtree_com[model.body_rootid[body]]
    return f, t + np.cross(s - point, f)


def worst_instant(comp: Compiled, model, plan, joints: list[str], link: str) -> int:
    """Отсчёт плана с наибольшим |M| реакции сустава звена у его оси."""
    mujoco = _mj()
    data = mujoco.MjData(model)
    b = model.body(link).id
    j = model.joint(link).id
    best, at = -1.0, 0
    for i in np.unique(np.linspace(0, len(plan.t) - 1, min(SCAN_SAMPLES, len(plan.t))).round().astype(int)):
        _state(model, data, comp, plan, joints, int(i))
        _, m = _wrench_at(model, data, b, data.xanchor[j])
        v = float(np.linalg.norm(m))
        if v > best:
            best, at = v, int(i)
    return at


def _bonded_groups(solids: list) -> list[list[int]]:
    """Детали, касающиеся друг друга, — группами (объединение по зазору)."""
    from datum.geom.measure import bbox, min_gap

    n = len(solids)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    boxes = [tuple(np.asarray(v, float) for v in bbox(s)) for s in solids]
    for a in range(n):
        for b in range(a + 1, n):
            (la, ha), (lb, hb) = boxes[a], boxes[b]
            if np.any(la > hb + BOND_GAP_MM) or np.any(lb > ha + BOND_GAP_MM):
                continue
            if min_gap(solids[a], solids[b], search=2 * BOND_GAP_MM) <= BOND_GAP_MM:
                parent[find(a)] = find(b)
    groups: dict[int, list[int]] = {}
    for a in range(n):
        groups.setdefault(find(a), []).append(a)
    return list(groups.values())


def _vertices(solid) -> np.ndarray:
    from datum.geom import man
    from datum.geom.convert import vertices_faces

    v, _ = vertices_faces(man(solid))
    return np.asarray(v, float)


def stress_body(comp: Compiled, link: str):
    """Несущее тело звена (объединение касающихся печатных деталей у оси сустава)
    и номера его деталей. Отдельно — чтобы сетки всех звеньев строились разом."""
    from datum.geom.booleans import union

    lk = comp.links[link]
    j = lk.joint
    printed = [k for k, p in enumerate(lk.pieces) if p["part"] is not None]
    if j is None or not printed:
        raise MechanismError(f"звено {link!r}: нет шарнира или печатных деталей")
    solids = [lk.solids[k] for k in printed]
    groups = _bonded_groups(solids)
    dist = [min(float(_axis_distance(_vertices(solids[k]), j.origin, j.axis).min()) for k in g)
            for g in groups]
    near = min(dist)
    structural = sorted(k for g, d in zip(groups, dist) if d <= near + STRUCT_REACH_MM for k in g)
    s_idx = [printed[k] for k in structural]
    return union([lk.solids[k] for k in s_idx], name=link), s_idx


def mesh_links(comp: Compiled, links: list[str], *, edge: float = 0.0) -> dict:
    """Лестницы сеток (ребро и вдвое крупнее) всех звеньев — ОДНОЙ очередью на все
    ядра (`tetmesh.tet_meshes`): звено → (тело, номера деталей, ребро, [сетки])."""
    from datum.fem.tetmesh import tet_meshes

    check_edge(edge)
    bodies = {}
    for n in links:
        try:
            bodies[n] = stress_body(comp, n)
        except MechanismError:
            continue                    # это звено скажет причину само, в link_stress
    links = list(bodies)
    if not links:
        return {}
    edges = {n: (edge if edge > 0 else mesh_edge(bodies[n][0])) for n in links}
    jobs = [(bodies[n][0], [edges[n], min(edges[n] * 2, 0.5)]) for n in links]
    meshes = tet_meshes(None, [], batch=jobs)
    return {n: (bodies[n][0], bodies[n][1], edges[n], meshes[k]) for k, n in enumerate(links)}


def link_stress(comp: Compiled, result, link: str, *, modulus: float, poisson: float = 0.35,
                strength_z: float | None = None, strength_xy: float | None = None,
                edge: float = 0.0, index: int | None = None, prepared=None) -> StressReport:
    """Напряжения в печатном звене `link` в худший момент прогона `result`.

    `edge` — ребро сетки долей диагонали; 0 — по толщине тела (`mesh_edge`)."""
    from datum.fem.loads import (Patch, boundary_triangles, components, facet_centroids,
                                 solve_loads, tie_pairs)
    from datum.fem.model import FemError, Material
    from datum.fem.tetmesh import mesher_available, tet_meshes
    from datum.fem.verify import patch_test
    from datum.sim.model import build_model

    mech = comp.mechanism
    check_edge(edge)
    if link not in comp.links:
        raise MechanismError(f"нет звена {link!r}; есть: {sorted(comp.links)}")
    lk = comp.links[link]
    if lk.joint is None or lk.joint.kind != "revolute":
        raise MechanismError(f"звено {link!r}: напряжения считаются у звена на ШАРНИРЕ — опора "
                             f"у оси его сустава; у основания и ползуна опора не определена")
    if not mesher_available():
        raise MechanismError("тетраэдрального мешера нет: pip install pytetwild")
    printed = [k for k, p in enumerate(lk.pieces) if p["part"] is not None]
    if not printed:
        raise MechanismError(f"звено {link!r}: печатных деталей нет — считать нечего")

    sc = mech.scenarios[result.scenario]
    model, joints = build_model(comp, payloads=sc.payloads)
    plan = result.plan
    i = worst_instant(comp, model, plan, joints, link) if index is None else int(index)

    # ---- несущая группа
    j = lk.joint
    body, s_idx = (prepared[0], prepared[1]) if prepared else stress_body(comp, link)
    names = [lk.pieces[k]["label"] for k in s_idx]
    loose_idx = [k for k in range(len(lk.pieces)) if k not in s_idx]

    # ---- состояние в худший момент
    mujoco = _mj()
    data = mujoco.MjData(model)
    _state(model, data, comp, plan, joints, i)
    b = model.body(link).id
    R = data.xmat[b].reshape(3, 3).copy()
    t = data.xpos[b].copy()

    def to_link(p_world):                                # мир (м) → звено (мм)
        return R.T @ (np.asarray(p_world) - t) * 1000.0

    acc = np.zeros(6)
    vel = np.zeros(6)
    mujoco.mj_objectAcceleration(model, data, mujoco.mjtObj.mjOBJ_BODY, b, acc, 0)
    mujoco.mj_objectVelocity(model, data, mujoco.mjtObj.mjOBJ_BODY, b, vel, 0)
    alpha, a_c, omega = acc[:3], acc[3:], vel[:3]
    c_w = data.xipos[b].copy()

    def proper_acc(x_mm: np.ndarray) -> np.ndarray:
        """«a − g» точки звена (м/с²) в системе звена; x — мм в системе звена."""
        r = t + R @ (np.asarray(x_mm, float) / 1000.0) - c_w
        return R.T @ (a_c + np.cross(alpha, r) + np.cross(omega, np.cross(omega, r)))

    # ---- нагрузки (система звена: Н, Н·мм, мм)
    from datum.geom.booleans import compose

    # имя, сила, момент, точка, ось (или None) и тело-партнёр, через касание с которым
    # нагрузка входит (следующее звено, навесное тело; у груза — нет)
    point_loads: list[tuple] = []
    for cname, clk in comp.links.items():
        if clk.parent != link:
            continue
        cb = model.body(cname).id
        partner = compose(clk.solids) if clk.solids else None
        if clk.joint is not None and clk.joint.kind == "revolute":
            cj = model.joint(cname).id
            o = data.xanchor[cj].copy()
            f, m = _wrench_at(model, data, cb, o)
            point_loads.append((f"звено {cname}", -(R.T @ f), -(R.T @ m) * 1000.0, to_link(o),
                                np.asarray(clk.joint.axis, float), partner))
        else:
            o = data.xipos[cb].copy()
            f, m = _wrench_at(model, data, cb, o)
            point_loads.append((f"звено {cname}", -(R.T @ f), -(R.T @ m) * 1000.0, to_link(o),
                                None, partner))
    for pname in sc.payloads:
        p = mech.payloads[pname]
        if p.link != link:
            continue
        pb = model.body(f"груз {pname}").id
        o = data.xpos[pb].copy()
        f, m = _wrench_at(model, data, pb, o)
        point_loads.append((f"груз {pname}", -(R.T @ f), -(R.T @ m) * 1000.0, to_link(o), None, None))
    for k in loose_idx:
        piece = lk.pieces[k]
        cm = np.asarray(piece["com_mm"], float)
        force = -piece["mass_g"] / 1000.0 * proper_acc(cm)
        point_loads.append((piece["label"], force, np.zeros(3), cm, None, lk.solids[k]))
    parent_solid = compose(comp.links[lk.parent].solids)

    structural_mass_kg = sum(lk.pieces[k]["mass_g"] for k in s_idx) / 1000.0

    material = Material(modulus, poisson)
    notes: list[str] = []
    auto = edge <= 0
    if prepared:
        edge = prepared[2]
    elif auto:
        edge = mesh_edge(body)
    fallbacks: set[str] = set()

    def solve(tm):
        from datum.geom.measure import bbox as _bbox
        from datum.geom.proximity import surface_distance

        edge_rel = tm.edge_relative
        mesh = tm.skfem()
        bf, tri = boundary_triangles(mesh)
        cen = facet_centroids(mesh, bf)
        # Куски, склеенные через зазор, — одно тело для опоры и нагрузок.
        ties = tie_pairs(mesh, BOND_GAP_MM + 2 * tm.envelope_mm)
        labels = components(mesh)
        if len(ties):
            node_comp = np.full(mesh.p.shape[1], -1)
            for kk in range(4):
                node_comp[mesh.t[kk]] = labels
            merge = {int(c): int(c) for c in np.unique(labels)}

            def root(x):
                while merge[x] != x:
                    x = merge[x]
                return x

            for a, b2 in zip(node_comp[ties[:, 0]], node_comp[ties[:, 1]]):
                merge[root(int(a))] = root(int(b2))
            labels = np.array([root(int(c)) for c in labels])
        f_comp = labels[mesh.f2t[0, bf]]
        cell = edge_rel * tm.diagonal
        band_axis = max(AXIS_BAND_MM, 3 * tm.envelope_mm, 0.5 * cell)
        band_point = max(POINT_BAND_MM, cell)
        reach = [g + tm.envelope_mm for g in CONTACT_GAPS_MM]

        def touching(solid, among: np.ndarray) -> np.ndarray:
            """Грани (маска по `bf`), которые касаются тела `solid`: центр грани ближе
            зазора к его поверхности или ушёл внутрь него (посадка с натягом)."""
            out = np.zeros(len(bf), bool)
            if solid is None:
                return out
            lo, hi = (np.asarray(v, float) for v in _bbox(solid))
            pad = max(reach) + CONTACT_INSIDE_MM
            box = among & np.all((cen >= lo - pad) & (cen <= hi + pad), axis=1)
            if not box.any():
                return out
            idx = np.where(box)[0]
            d, inside = surface_distance(solid, cen[idx], spacing=CONTACT_SPACING_MM)
            out[idx[(d <= max(reach)) | (inside & (d <= CONTACT_INSIDE_MM))]] = True
            return out

        def by_axis(origin, axis) -> np.ndarray:
            d = _axis_distance(cen, origin, axis)
            picked = []
            near = [(float(d[f_comp == c].min()), c) for c in np.unique(f_comp)]
            closest = min(r for r, _ in near)
            for r, c in near:
                if r <= closest + STRUCT_REACH_MM:
                    picked.append(bf[(f_comp == c) & (d <= r + band_axis)])
            return np.concatenate(picked)

        def at_axis(origin, axis, partner, what: str) -> np.ndarray:
            """Поверхность у оси, через которую идёт сустав: касание с партнёром (вал
            в отверстии, подшипник в гнезде, торец о щёку) не дальше от оси, чем
            ближайшее касание + запас; не нашлось — ближайшая к оси поверхность."""
            d = _axis_distance(cen, origin, axis)
            hit = touching(partner, d <= AXIS_CONTACT_MAX_MM)
            if hit.any():
                dmin = float(d[hit].min())
                hit &= d <= dmin + max(AXIS_CONTACT_REACH_MM, 0.5 * dmin)
                found = bf[hit]
                # у куска без своего касания — правило оси (иначе он висел бы в пустоте)
                missing = [c for c in np.unique(f_comp) if not np.isin(c, f_comp[hit])]
                if missing:
                    rest = by_axis(origin, axis)
                    extra = rest[np.isin(labels[mesh.f2t[0, rest]], missing)]
                    if extra.size:
                        fallbacks.add(what)
                    found = np.union1d(found, extra)
                return found
            fallbacks.add(what)
            return by_axis(origin, axis)

        def at_point(p, partner, what: str) -> np.ndarray:
            if partner is not None:
                hit = touching(partner, np.ones(len(bf), bool))
                if hit.any():
                    return bf[hit]
            dd = np.linalg.norm(cen - np.asarray(p, float), axis=1)
            if partner is not None:
                fallbacks.add(what)
            return bf[dd <= dd.min() + band_point]

        fixed = at_axis(j.origin, j.axis, parent_solid, "опора")
        patches = []
        for name, force, moment, at, axis, partner in point_loads:
            facets = (at_axis(at, axis, partner, name) if axis is not None
                      else at_point(at, partner, name))
            # пятно не может лечь на опору: сила ушла бы прямо в заделку
            facets = np.setdiff1d(facets, fixed)
            if facets.size == 0:
                raise FemError(f"нагрузка «{name}» ложится на опору звена — у неё нет своей "
                               f"поверхности; звено считать так нельзя")
            patches.append(Patch(name, facets, force, moment, at))
        rho = structural_mass_kg / max(tm.volume, 1e-9)            # кг/мм³
        b0 = -rho * proper_acc(np.zeros(3))
        B = np.column_stack([-rho * proper_acc(np.eye(3)[k]) - b0 for k in range(3)])
        sol = solve_loads(tm, material, fixed=fixed, patches=patches, body=(b0, B), ties=ties)
        # Зона, где пик не судится: опора И места приложения нагрузки (Сен-Венан:
        # напряжение там — про способ приложения, а не про деталь).
        zones = np.concatenate([fixed] + [p.facets for p in patches])
        centres = facet_centroids(mesh, zones)
        if len(ties):
            # у склейки напряжение — про стык (винт, клей), а не про деталь
            centres = np.vstack([centres, mesh.p.T[np.unique(ties)]])
        return tm, mesh, tri, sol, centres, cell

    try:
        # Обе сетки — разом, в разных процессах: мешер — три четверти всего времени.
        # Мешер СЛУЧАЕН и изредка оставляет почти плоские тетраэдры: матрица тогда
        # обусловлена плохо, и тест заплаты на этой сетке не проходит (сердце Gear
        # Cube: ошибка 2e-8…6e-8 при допуске 1e-9 одним прогоном, 3e-11 другим — на
        # обоих решателях одинаково). Такую сетку не судят, а строят заново; не
        # вышло за MESH_ATTEMPTS — отказ с причиной, а не числа.
        failures: list[str] = []
        for attempt in range(MESH_ATTEMPTS):
            meshes = (prepared[3] if prepared and attempt == 0
                      else tet_meshes(body, [edge, min(edge * 2, 0.5)]))
            try:
                tm, mesh, tri, fine, fixed_fine, cell = solve(meshes[0])
            except FemError as e:
                if "без опоры" not in str(e):
                    raise                               # геометрия, а не случай сетки
                failures.append(f"сетка {attempt + 1}: {e}")
                continue
            check = patch_test(mesh, material, stiffness=fine.stiffness)
            if not check.ok:
                worst = max(c.error for c in check.cases)
                failures.append(f"сетка {attempt + 1}: тест заплаты {worst:.1e} (худший "
                                f"тетраэдр {tm.worst_quality:.3f})")
                continue
            try:
                _, _, _, coarse, fixed_coarse, _ = solve(meshes[1])
            except FemError as e:
                if "без опоры" not in str(e):
                    raise
                failures.append(f"грубая сетка {attempt + 1}: {e}")
                continue
            break
        else:
            raise MechanismError(
                f"на {MESH_ATTEMPTS} сетках подряд решатель не воспроизвёл эталоны или куски "
                f"не склеились — числам верить нельзя ({'; '.join(failures)}). Обычно это "
                f"очень тонкие места или детали, касающиеся по линии: другое ребро сетки "
                f"(edge) или упрощение несущих деталей звена")
        if failures:
            notes.append(f"сетка построена заново ({'; '.join(failures)}): мешер случаен, и "
                         f"на плохой сетке числа не называются")
    except FemError as e:
        raise MechanismError(f"звено {link!r}: {e}") from e

    # Пик судится ВНЕ зоны опоры и мест приложения нагрузки — одной на обеих сетках. У самой опоры
    # (отверстие «приклеено» к валу) он растёт с каждым измельчением: на рычаге
    # 5.3 → 8.4 → 11.6 → 13.7 МПа, тогда как тело сходилось (99 % — 4.4…4.7).
    zone = max(SUPPORT_ZONE_MM, 1.5 * cell)
    peak_f, at_f = fine.peak_outside(fixed_fine, zone)
    peak_c, _ = coarse.peak_outside(fixed_coarse, zone)
    if not np.isfinite(peak_f) or not np.isfinite(peak_c):
        peak_f, at_f, peak_c = fine.peak_vm, fine.peak_at, coarse.peak_vm
        notes.append("всё тело — у опоры и мест приложения нагрузки: пик судится вместе с ними")
    change = abs(peak_f - peak_c) / max(peak_f, peak_c, 1e-30)
    growth = (peak_f - peak_c) / max(peak_c, 1e-30)
    p99_change = abs(fine.vm_p99 - coarse.vm_p99) / max(fine.vm_p99, 1e-30)
    # С пределом — ВСЕГДА больший пик вне опоры двух сеток (оценка сверху). Мешер не
    # детерминирован: на плече руки от запуска к запуску пик то сходился, то «рос с
    # измельчением», и прежняя подмена пика 99-м процентилем роняла оценку с ~5 до
    # 1.5 МПа — вердикт прыгал в сторону «прошло». Подозрение на особенность — словами.
    value = max(peak_f, peak_c)
    basis = "пик вне опоры и мест нагрузки"
    if growth > SINGULAR_GROWTH and p99_change < CONVERGED:
        basis += ", растёт с измельчением — возможна особенность"
        notes.append(f"пик растёт с измельчением ({peak_c:.2f} → {peak_f:.2f} МПа) при "
                     f"сошедшемся 99-м процентиле ({fine.vm_p99:.2f}) — похоже на особенность у "
                     f"пятна нагрузки или острого угла: сравнивается пик (оценка сверху); "
                     f"реальное скругление в модели снимет вопрос")
    elif change >= CONVERGED:
        basis += f", сетки расходятся на {change * 100:.0f} % — оценка сверху"
        notes.append(f"пик вне опоры на двух сетках {peak_c:.2f} и {peak_f:.2f} МПа: взят "
                     f"больший; мельче ребро (edge) — точнее")

    if fine.peak_vm > value * 1.05:
        notes.append(f"у опоры, мест приложения нагрузки и стыков деталей (ближе {zone:.1f} мм) — до "
                     f"{fine.peak_vm:.2f} МПа: там напряжение зависит от того, как вал сидит в "
                     f"отверстии и как нагрузка входит, а модель склеивает их с деталью; посадку "
                     f"и смятие стенки отверстия считают отдельно (pin_joint)")

    # Сверка с MuJoCo: приложенное к звену = −(сила родителя на звено).
    f_par, m_par = _wrench_at(model, data, b, data.xanchor[model.joint(link).id])
    o_l = to_link(data.xanchor[model.joint(link).id])
    fa, ma = fine.applied
    ma_o = ma - np.cross(o_l, fa)                               # момент приложенного о оси звена
    f_ref, m_ref = R.T @ f_par, R.T @ m_par * 1000.0
    scale = max(float(np.linalg.norm(f_ref)) + float(np.linalg.norm(m_ref)) / fine.size_mm, 1e-12)
    mismatch = (float(np.linalg.norm(fa + f_ref)) + float(np.linalg.norm(ma_o + m_ref)) / fine.size_mm) / scale

    if loose_idx:
        notes.append("навесные тела (" + ", ".join(lk.pieces[k]["label"] for k in loose_idx)
                     + ") — их вес и силы инерции в их центре масс, через поверхность касания с "
                     "несущим телом (нет касания — ближайшую); сами они не считаются")
    if j.follows is not None:
        notes.append(f"звено ведомое (зацепление с «{j.follows[0]}»): в модели это связь, а не "
                     f"контакт зубьев — момент, который передают зубья, расчёт кладёт на ось; "
                     f"зубья не считаются (для них — изгиб у корня зуба по модулю и ширине)")
    if len(s_idx) > 1:
        notes.append("детали несущей группы считаются склеенными по касанию — у винтовых "
                     "стыков это оптимистично")
    notes.append("материал изотропный; предел — между слоями (MAT_TENSILE_Z), слабейший: "
                 "где растяжение идёт вдоль слоёв, запас больше")

    loads = [{"name": name, "force_n": [round(float(v), 4) for v in force],
              "moment_nmm": [round(float(v), 3) for v in moment],
              "at_mm": [round(float(v), 2) for v in at],
              "area_mm2": fine.patch_areas.get(name)}
             for name, force, moment, at, _, _ in point_loads]
    if fallbacks:
        notes.append("касания с соседним телом не нашлось (" + ", ".join(sorted(fallbacks))
                     + ") — там опора/пятно взяты по ближайшей к оси или к точке поверхности")
    return StressReport(
        link=link, t=float(plan.t[i]), index=i, pieces=names,
        loose=[lk.pieces[k]["label"] for k in loose_idx],
        value_mpa=float(value), basis=basis, peak_mpa=float(peak_f), peak_coarse_mpa=float(peak_c),
        p99_mpa=fine.vm_p99, p99_coarse_mpa=coarse.vm_p99, support_peak_mpa=fine.peak_vm,
        peak_at=tuple(at_f),
        max_displacement_mm=fine.max_displacement, strength_z=strength_z, strength_xy=strength_xy,
        imbalance=fine.imbalance, mujoco_mismatch=mismatch, loads=loads,
        mesh={"nodes": tm.nodes, "cells": tm.cells, "worst_quality": round(tm.worst_quality, 4),
              "edge": round(edge, 5), "edge_mm": round(cell, 3), "auto": auto,
              "fixed_area_mm2": round(fine.fixed_area, 2)},
        notes=notes, verts=mesh.p.T.copy(), faces=tri, node_vm=fine.node_vm)


def heat_rgb(u: np.ndarray) -> np.ndarray:
    """Доля предела → цвет (0…255), та же шкала, что у карты загрузки окна (`heatColor`)."""
    stops = np.array([0.0, 0.5, 0.8, 1.0])
    cols = np.array([[47, 214, 138], [255, 212, 71], [255, 157, 60], [255, 59, 78]], float)
    u = np.asarray(u, float)
    u = np.where(np.isfinite(u), np.clip(u, 0.0, 1.0), 1.0)
    return np.stack([np.interp(u, stops, cols[:, k]) for k in range(3)], axis=-1)
