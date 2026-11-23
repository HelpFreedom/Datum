"""Тело под произвольной нагрузкой: опора, силовые пятна и объёмная сила.

`solve_faces` ставит задачу «крайняя грань защемлена, крайняя грань нагружена»
— этого хватает для «прижать основание, надавить сверху». Звену механизма в
худший момент нужно другое: опора — поверхность у оси СВОЕГО сустава (отверстие
под вал), нагрузки — сила и МОМЕНТ от следующего звена у его оси, грузы, тела,
закреплённые на звене, и собственный вес с силами инерции, распределённые по
объёму. Здесь только механика; кто где и сколько — решает `sim.stress`.

Пятно с силой F и моментом M (о точке `about`) нагружается так, как болтовое
соединение раздаёт нагрузку по жёсткому фланцу: t(x) = F/A + ω×(x − c), где
c — центр пятна, ω = I⁻¹·M_c, I = ∫(|r|²E − r·rᵀ) dA, r = x − c. Тогда
∫t dA = F и ∫r×t dA = M_c ровно (сверено тестом), а чистый момент вокруг оси
отверстия раздаётся касательной нагрузкой по его стенке — как передаёт его вал.

Объёмная сила — аффинная: f(x) = b0 + B·x (Н/мм³, x в мм). Поле ускорений
жёсткого тела аффинно по точке (a = a_c + α×r + ω×(ω×r)), поэтому вес и силы
инерции звена передаются ТОЧНО, а не сосредоточенной силой в центре масс.

Самопроверка — равновесие: реакция заделки, посчитанная по решению (K·u − f
на закреплённых степенях свободы), обязана уравновесить приложенное. Это
ловит потерянное пятно, ошибку знака и ошибку площади — то, от чего число
выглядит правдоподобным и врёт.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from datum.fem.model import FemError, Material

__all__ = ["Patch", "StressSolution", "solve_loads", "tie_pairs", "von_mises", "boundary_triangles",
           "facet_centroids", "components"]


@dataclass(frozen=True)
class Patch:
    """Силовое пятно: грани сетки skfem, сила (Н) и момент (Н·мм) о точке `about` (мм)."""

    name: str
    facets: np.ndarray
    force: np.ndarray
    moment: np.ndarray = field(default_factory=lambda: np.zeros(3))
    about: np.ndarray = field(default_factory=lambda: np.zeros(3))


@dataclass(frozen=True)
class StressSolution:
    u: np.ndarray = field(repr=False)
    dofs: int = 0
    ms: float = 0.0
    #: Эквивалентное напряжение по Мизесу, МПа: пик в точках интегрирования, где он, 99-й процентиль.
    peak_vm: float = 0.0
    peak_at: tuple[float, float, float] = (0.0, 0.0, 0.0)
    vm_p99: float = 0.0
    #: Наибольшая главная деформация (модуль) и наибольшее главное растягивающее напряжение.
    peak_strain: float = 0.0
    peak_tension: float = 0.0
    max_displacement: float = 0.0
    #: Мизес в узлах-вершинах (среднее по тетраэдрам вокруг узла), МПа — для карты.
    node_vm: np.ndarray = field(default=None, repr=False)          # type: ignore[assignment]
    #: Сумма приложенного (сила Н, момент Н·мм о начале) и реакция заделки — для равновесия.
    applied: tuple[np.ndarray, np.ndarray] = field(default=None, repr=False)   # type: ignore[assignment]
    reaction: tuple[np.ndarray, np.ndarray] = field(default=None, repr=False)  # type: ignore[assignment]
    patch_areas: dict = field(default_factory=dict)
    fixed_area: float = 0.0
    #: Диагональ габарита сетки, мм — мера, которой сила и момент приводятся друг к другу.
    size_mm: float = 1.0
    #: Точки интегрирования (k×3, мм) и Мизес в них — чтобы судить пик ВНЕ зоны опоры.
    qp_points: np.ndarray = field(default=None, repr=False)        # type: ignore[assignment]
    qp_vm: np.ndarray = field(default=None, repr=False)            # type: ignore[assignment]
    #: Собранная матрица жёсткости — тест заплаты на той же сетке берёт её, а не собирает снова.
    stiffness: object = field(default=None, repr=False)

    def peak_outside(self, centres: np.ndarray, radius: float) -> tuple[float, tuple]:
        """Наибольший Мизес дальше `radius` от точек `centres` (центры граней опоры).

        У защемлённой поверхности напряжение растёт с измельчением без предела —
        это свойство условия «приклеено», а не детали (принцип Сен-Венана: как
        именно вал держит отверстие, решает напряжение только рядом с ним)."""
        from scipy.spatial import cKDTree

        d, _ = cKDTree(np.asarray(centres, float)).query(self.qp_points)
        mask = d > radius
        if not mask.any():
            return float("nan"), (float("nan"),) * 3
        k = int(np.argmax(np.where(mask, self.qp_vm, -np.inf)))
        return float(self.qp_vm[k]), tuple(float(v) for v in self.qp_points[k])

    @property
    def imbalance(self) -> tuple[float, float]:
        """Невязка равновесия: |ΣF| и |ΣM| долями приложенного.

        Сила и момент нормируются ОДНОЙ мерой (|F| + |M|/размер): у чистого момента
        приложенная сила — ноль, и деление на неё объявляло бы невязку огромной."""
        fa, ma = self.applied
        fr, mr = self.reaction
        scale = max(float(np.linalg.norm(fa)) + float(np.linalg.norm(ma)) / self.size_mm, 1e-12)
        return (float(np.linalg.norm(fa + fr)) / scale,
                float(np.linalg.norm(ma + mr)) / self.size_mm / scale)


def _element():
    from datum.fem.solve import _element as e

    return e()


def facet_centroids(mesh, facets: np.ndarray) -> np.ndarray:
    """Центры граней (k×3, мм)."""
    return mesh.p[:, mesh.facets[:, facets]].mean(axis=1).T


def boundary_triangles(mesh) -> tuple[np.ndarray, np.ndarray]:
    """Наружные грани сетки, обход — наружу: (индексы граней, треугольники k×3 по узлам)."""
    bf = mesh.boundary_facets()
    tri = mesh.facets[:, bf].T.copy()
    tet = mesh.f2t[0, bf]
    p = mesh.p.T
    a, b, c = p[tri[:, 0]], p[tri[:, 1]], p[tri[:, 2]]
    n = np.cross(b - a, c - a)
    inside = p[mesh.t[:, tet].T].mean(axis=1)                # центр своего тетраэдра
    flip = np.einsum("ij,ij->i", n, inside - a) > 0          # нормаль смотрит внутрь
    tri[flip] = tri[flip][:, [0, 2, 1]]
    return bf, tri


def components(mesh) -> np.ndarray:
    """Номер связного куска для каждого тетраэдра (тела, не касающиеся друг друга)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    inner = mesh.f2t[1] >= 0
    a, b = mesh.f2t[0, inner], mesh.f2t[1, inner]
    n = mesh.t.shape[1]
    g = coo_matrix((np.ones(len(a)), (a, b)), shape=(n, n))
    _, labels = connected_components(g, directed=False)
    return labels


def von_mises(sig: np.ndarray) -> np.ndarray:
    """Мизес по тензору напряжений (…×3×3)."""
    s = sig - np.trace(sig, axis1=-2, axis2=-1)[..., None, None] * np.eye(3) / 3.0
    return np.sqrt(1.5 * np.einsum("...ij,...ij->...", s, s))


def _patch_load(mesh, elem, patch: Patch):
    """Линейная форма пятна и его площадь. Распределение — как у жёсткого фланца."""
    from skfem import FacetBasis, LinearForm, asm

    fb = FacetBasis(mesh, elem, facets=patch.facets)
    x = fb.global_coordinates().value                         # 3×грани×точки
    w = fb.dx                                                 # грани×точки
    area = float(w.sum())
    if area <= 0:
        raise FemError(f"пятно «{patch.name}»: площадь вышла нулевой")
    c = np.einsum("kij,ij->k", x, w) / area
    r = x - c[:, None, None]
    rr = np.einsum("kij,kij->ij", r, r)
    inertia = (np.einsum("ij,ij->", rr, w) * np.eye(3)
               - np.einsum("kij,lij,ij->kl", r, r, w))
    force = np.asarray(patch.force, float)
    moment_c = np.asarray(patch.moment, float) + np.cross(np.asarray(patch.about, float) - c, force)
    # Пятно на прямой (вырожденный тензор) момент вокруг этой прямой не передаёт:
    # псевдообратная отдаёт то, что передать может, а невязка видна в равновесии.
    omega = np.linalg.pinv(inertia, rcond=1e-9) @ moment_c
    base = force / area

    @LinearForm
    def form(v, wf):
        rx = wf.x[0] - c[0]
        ry = wf.x[1] - c[1]
        rz = wf.x[2] - c[2]
        tx = base[0] + omega[1] * rz - omega[2] * ry
        ty = base[1] + omega[2] * rx - omega[0] * rz
        tz = base[2] + omega[0] * ry - omega[1] * rx
        return tx * v[0] + ty * v[1] + tz * v[2]

    return asm(form, fb), area


def tie_pairs(mesh, gap: float) -> np.ndarray:
    """Пары узлов (k×2), склеивающие РАЗНЫЕ куски сетки там, где они касаются.

    Детали звена, стянутые винтами и касающиеся друг друга, считаются склеенными; но
    объединение тел без перекрытия оставляет их отдельными кусками — и кусок, не
    касающийся опоры, висел бы в пустоте (башня руки, оси сердца Gear Cube).

    Узел поверхности куска связывается, если он лежит НА поверхности соседа (точное
    расстояние до его треугольников не больше `gap`), — с ближайшим узлом соседа.
    Прежде связь искалась «узел к узлу в пределах зазора»: у касающихся граней узлы
    мешера не совпадают, пар находилось несколько случайных, а то и ни одной — то
    выброс напряжения у редкой пружины, то «кусок висит в пустоте»."""
    from scipy.spatial import cKDTree

    from datum.geom.proximity import mesh_distance

    labels = components(mesh)
    if labels.max() == 0:
        return np.zeros((0, 2), np.int64)
    _, tri = boundary_triangles(mesh)
    tri_comp = labels[mesh.f2t[0, mesh.boundary_facets()]]
    node_comp = np.full(mesh.p.shape[1], -1)
    for k in range(4):
        node_comp[mesh.t[k]] = labels
    surf = np.unique(tri)
    pts = mesh.p.T
    out = []
    for c in np.unique(labels):
        mine = surf[node_comp[surf] == c]
        other_tri = tri[tri_comp != c]
        if not len(mine) or not len(other_tri):
            continue
        d, _ = mesh_distance(pts, other_tri, pts[mine], spacing=max(gap, 0.5))
        touch = mine[d <= gap]
        if not len(touch):
            continue
        other_nodes = np.unique(other_tri)
        _, j = cKDTree(pts[other_nodes]).query(pts[touch])
        out.append(np.column_stack([touch, other_nodes[j]]))
    return np.concatenate(out) if out else np.zeros((0, 2), np.int64)


def solve_loads(tetmesh, material: Material, *, fixed: np.ndarray, patches: list[Patch],
                body: tuple[np.ndarray, np.ndarray] | None = None,
                ties: np.ndarray | None = None) -> StressSolution:
    """Решить: грани `fixed` защемлены, пятна нагружены, по объёму — f = b0 + B·x.

    Каждый связный кусок сетки обязан иметь закреплённые грани: иначе система
    вырождена (кусок висит в пустоте), и решатель вернул бы мусор."""
    from skfem import Basis, FacetBasis, LinearForm, asm
    from skfem.helpers import sym_grad

    from datum.fem.solve import _solve, _stiffness

    t0 = time.perf_counter()
    mesh = tetmesh.skfem()
    elem = _element()
    basis = Basis(mesh, elem)
    K = K0 = _stiffness(basis, material)            # K0 — без склеек: её берёт тест заплаты
    fixed = np.unique(np.asarray(fixed, dtype=np.int64))
    if fixed.size == 0:
        raise FemError("опора не нашлась: ни одной закреплённой грани")
    labels = components(mesh)
    ties = np.zeros((0, 2), np.int64) if ties is None else np.asarray(ties, np.int64)
    if len(ties):
        # Склейка — жёсткой пружиной между узлами через зазор, по каждой оси: k = 10·E·h
        # (h — ребро сетки), на порядок жёстче элемента рядом, но без вырождения матрицы.
        import scipy.sparse as sp

        h = float(np.linalg.norm(tetmesh.points.max(0) - tetmesh.points.min(0))
                  * tetmesh.edge_relative)
        k_tie = 10.0 * material.modulus * max(h, 1e-3)
        nd = basis.nodal_dofs
        rows, cols, vals = [], [], []
        for c in range(3):
            a, b = nd[c, ties[:, 0]], nd[c, ties[:, 1]]
            rows += [a, b, a, b]
            cols += [a, b, b, a]
            vals += [np.full(len(a), k_tie)] * 2 + [np.full(len(a), -k_tie)] * 2
        K = K + sp.coo_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
                              shape=K.shape).tocsr()
        # куски, связанные склейкой, держатся вместе
        node_comp = np.full(mesh.p.shape[1], -1)
        for kk in range(4):
            node_comp[mesh.t[kk]] = labels
        parent = list(range(int(labels.max()) + 1))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a, b in zip(node_comp[ties[:, 0]], node_comp[ties[:, 1]]):
            parent[find(int(a))] = find(int(b))
        labels = np.array([find(int(c)) for c in labels])
    held = set(labels[mesh.f2t[0, fixed]].tolist())
    loose = sorted(set(labels.tolist()) - held)
    if loose:
        raise FemError(f"кусков сетки без опоры: {len(loose)} из {labels.max() + 1} — они "
                       f"висят в пустоте, и задача вырождена")

    f = np.zeros(basis.N)
    areas = {}
    applied_f = np.zeros(3)
    applied_m = np.zeros(3)
    for p in patches:
        if len(p.facets) == 0:
            raise FemError(f"пятно «{p.name}»: ни одной грани")
        vec, a = _patch_load(mesh, elem, p)
        f += vec
        areas[p.name] = round(a, 3)
        fp = np.asarray(p.force, float)
        applied_f += fp
        applied_m += np.asarray(p.moment, float) + np.cross(np.asarray(p.about, float), fp)
    if body is not None:
        b0, B = (np.asarray(v, float) for v in body)

        @LinearForm
        def bodyform(v, w):
            fx = b0[0] + B[0, 0] * w.x[0] + B[0, 1] * w.x[1] + B[0, 2] * w.x[2]
            fy = b0[1] + B[1, 0] * w.x[0] + B[1, 1] * w.x[1] + B[1, 2] * w.x[2]
            fz = b0[2] + B[2, 0] * w.x[0] + B[2, 1] * w.x[1] + B[2, 2] * w.x[2]
            return fx * v[0] + fy * v[1] + fz * v[2]

        f += asm(bodyform, basis)
        # Сумма объёмной силы и её момент — квадратурой по тем же точкам.
        x = basis.mapping.F(basis.X)                       # 3×элементы×точки
        dx = basis.dx
        fx = b0[:, None, None] + np.einsum("kl,lij->kij", B, x)
        applied_f += np.einsum("kij,ij->k", fx, dx)
        applied_m += np.einsum("kij,ij->k", np.cross(x, fx, axis=0), dx)

    D = basis.get_dofs(fixed).flatten()
    u = _solve(basis, K, f, D)

    # Реакция заделки: K·u − f на закреплённых степенях свободы (узлы — doflocs).
    resid = K @ u - f
    loc = basis.doflocs
    comp = np.arange(basis.N) % 3
    rf = np.array([resid[D][comp[D] == k].sum() for k in range(3)])
    # Момент реакции: Σ r × R по закреплённым узлам (у P2 узлы — вершины и середины рёбер).
    Rv = np.zeros((len(D), 3))
    Rv[np.arange(len(D)), comp[D]] = resid[D]
    rm = np.cross(loc[:, D].T, Rv).sum(axis=0)

    lam, mu = material.lame
    field_ = basis.interpolate(u)
    eps = np.moveaxis(sym_grad(field_), [0, 1], [-2, -1])       # элементы×точки×3×3
    tr = np.trace(eps, axis1=-2, axis2=-1)
    sig = lam * tr[..., None, None] * np.eye(3) + 2 * mu * eps
    vm = von_mises(sig)
    eig_e = np.linalg.eigvalsh(eps)
    eig_s = np.linalg.eigvalsh(sig)
    where = np.unravel_index(int(np.argmax(vm)), vm.shape)
    points = basis.mapping.F(basis.X)
    at = (float(points[0][where]), float(points[1][where]), float(points[2][where]))

    # Мизес в вершинах: среднее по тетраэдрам вокруг узла (для карты, не для вердикта).
    elem_vm = vm.mean(axis=1)
    nv = mesh.p.shape[1]
    acc = np.zeros(nv)
    cnt = np.zeros(nv)
    for k in range(4):
        np.add.at(acc, mesh.t[k], elem_vm)
        np.add.at(cnt, mesh.t[k], 1)
    node_vm = acc / np.maximum(cnt, 1)

    hold_area = float(FacetBasis(mesh, elem, facets=fixed).dx.sum())
    disp = np.sqrt(sum(u[k::3] ** 2 for k in range(3)))
    return StressSolution(
        u=u, dofs=int(basis.N), ms=(time.perf_counter() - t0) * 1000,
        peak_vm=float(vm[where]), peak_at=at, vm_p99=float(np.percentile(vm, 99)),
        peak_strain=float(np.abs(eig_e).max()), peak_tension=float(eig_s.max()),
        max_displacement=float(disp.max()), node_vm=node_vm,
        applied=(applied_f, applied_m), reaction=(rf, rm), patch_areas=areas,
        fixed_area=hold_area, size_mm=float(tetmesh.diagonal),
        qp_points=np.stack([points[k].ravel() for k in range(3)], axis=1), qp_vm=vm.ravel(),
        stiffness=K0)
