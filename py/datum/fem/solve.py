"""Решение линейной упругости на призматическом брусе.

ЭЛЕМЕНТ ТОЛЬКО P2, и это вывод из замера, а не осторожность. На консоли
40×10×2 линейные тетраэдры дали:

    P1  10×3×1   264 стп. свободы   δ = 0.19 мм   −88 % от формулы
    P1  60×15×6  20496              δ = 1.26 мм   −21 %
    P2  10×3×1  1323                δ = 1.52 мм    −5 %

Это запирание на сдвиге: линейный тетраэдр не умеет изгибаться и подменяет
изгиб сдвигом, оттого выходит во много раз жёстче. На чистом изгибе (где
точное решение квадратично и P2 обязан дать его ТОЧНО) разрыв ещё нагляднее:
P2 ошибается на 1e-11, P1 — на 66…96 %. Предлагать P1 значило бы предлагать
грабли; он здесь не поддерживается вовсе.

ТРИ ЗАДАЧИ, А НЕ ОБЩИЙ ИНТЕРФЕЙС. Растяжение, чистый изгиб и консоль с силой
на конце покрывают и самопроверку, и то, ради чего решатель заводился. Общий
способ задавать закрепления и нагрузки был бы выдумкой под несуществующего
пользователя; появится задача — появится и обобщение.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace

import numpy as np

from datum.fem.model import Bar, FemError, Material

__all__ = ["Solution", "tension", "pure_bending", "tip_load", "guided_deflection",
           "Divisions"]

#: Разбиение сетки: по длине, ширине, толщине.
Divisions = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class Solution:
    """Результат одного решения."""

    u: np.ndarray = field(repr=False)
    #: Координаты степеней свободы, 3×N. Компонента = индекс mod 3.
    doflocs: np.ndarray = field(repr=False)
    dofs: int = 0
    divisions: Divisions = (0, 0, 0)
    ms: float = 0.0
    #: Наибольшая по модулю главная деформация во всём теле, где она, и
    #: 99-й процентиль (почти не чувствителен к сингулярностям).
    peak_strain: float = 0.0
    peak_at: tuple[float, float, float] = (0.0, 0.0, 0.0)
    strain_p99: float = 0.0
    #: Сколько граней сетки попало в защемление и в нагрузку, и какой допуск
    #: при этом брался. Частичный отбор иначе не заметить.
    fixed_facets: int = 0
    load_facets: int = 0
    #: Площади отобранных граней. Число граней о полноте отбора НЕ говорит:
    #: триангуляция у неструктурированной сетки разная, и восемь крупных
    #: треугольников покрывают ту же грань, что десять мелких. А вот площадь
    #: говорит — по ней и видно, что защемлена вся грань, а не её часть.
    fixed_area: float = 0.0
    load_area: float = 0.0
    face_tolerance: float = 0.0

    def component(self, axis: int) -> np.ndarray:
        """Все значения одной компоненты перемещения."""
        return self.u[axis::3]

    def locations(self, axis: int) -> np.ndarray:
        return self.doflocs[:, axis::3]

    def at_face(self, axis: int, coordinate: float, component: int) -> np.ndarray:
        """Перемещения `component` в узлах грани `axis = coordinate`."""
        loc = self.locations(component)
        mask = np.isclose(loc[axis], coordinate)
        if not mask.any():
            raise FemError(f"в сетке нет узлов с координатой {axis}={coordinate}")
        return self.component(component)[mask]


# ------------------------------------------------------------------ внутреннее
def _element():
    """Элемент — В ОДНОМ МЕСТЕ.

    Он задавался в двух: в базисе тела и отдельно в каждом `FacetBasis` для
    нагрузки. Саботаж (подменить элемент) это и вскрыл: базисы разошлись,
    растяжение вернуло НОЛЬ вместо точного ответа, и ни одного исключения при
    этом не возникло. Два источника правды об одном и том же — приглашение к
    такой беде.
    """
    from skfem import ElementTetP2, ElementVector

    return ElementVector(ElementTetP2())


def _end_facets(mesh, basis, x: float):
    """Базис на торце x = const — куда прикладывается нагрузка."""
    from skfem import FacetBasis

    import numpy as _np

    facets = mesh.facets_satisfying(lambda p: _np.isclose(p[0], x))
    if len(facets) == 0:
        raise FemError(f"в сетке нет граней на торце x={x}")
    return FacetBasis(mesh, basis.elem, facets=facets)


def _pieces(bar: Bar, divisions: Divisions):
    from skfem import Basis, MeshTet

    nx, ny, nz = divisions
    if min(divisions) < 1:
        raise FemError(f"разбиение должно быть не меньше 1 по каждой оси, получено {divisions}")
    # Плоскости симметрии y=0 и z=0 обязаны быть плоскостями УЗЛОВ: на них
    # ставятся условия, согласованные с точным решением. При нечётном числе
    # делений такой плоскости в сетке нет, условие ложится в пустоту, жёсткое
    # смещение остаётся незакреплённым — и решение выходит бессмысленным.
    # Проверено: именно так первая версия этого модуля выдала nan.
    if ny % 2 or nz % 2:
        raise FemError(
            f"число делений по ширине и толщине должно быть чётным, получено "
            f"{ny} и {nz}: иначе в сетке нет плоскостей симметрии y=0 и z=0, "
            f"на которых стоят закрепления")

    mesh = MeshTet.init_tensor(
        np.linspace(0.0, bar.length, nx + 1),
        np.linspace(-bar.width / 2, bar.width / 2, ny + 1),
        np.linspace(-bar.thickness / 2, bar.thickness / 2, nz + 1))
    basis = Basis(mesh, _element())
    return mesh, basis


#: Элементов в куске пакетной сборки: ~120 МБ промежуточных массивов у P2.
STIFFNESS_CHUNK = 4000


def _stiffness(basis, material: Material):
    """Матрица жёсткости — пакетно, той же квадратурой и нумерацией, что `asm`.

    `skfem.asm` собирает питоновским циклом по парам функций формы (у P2 — 900 пар),
    на одном ядре: плечо руки (31.5 тыс. тетраэдров) — 73 с, и `nthreads` не
    помогал (81 с). Здесь те же интегралы — одним произведением матриц по всем
    элементам сразу (BLAS, все ядра). Совпадение с `asm` держит тест."""
    import scipy.sparse as sp

    lam, mu = material.lame
    n_loc = len(basis.basis)
    dx = basis.dx                                            # элементы×точки
    E, Q = dx.shape
    grads = [np.asarray(basis.basis[i][0].grad) for i in range(n_loc)]   # 3×3×E×Q
    dofs = basis.element_dofs                                # i×E
    rows, cols, vals = [], [], []
    # Кусками по элементам: целиком промежуточные массивы плеча руки — 1.5 ГБ.
    for s0 in range(0, E, STIFFNESS_CHUNK):
        sl = slice(s0, min(s0 + STIFFNESS_CHUNK, E))
        e_n = sl.stop - sl.start
        eps = np.empty((e_n, n_loc, Q, 9))
        for i, g in enumerate(grads):
            gi = g[:, :, sl, :]
            e = 0.5 * (gi + np.swapaxes(gi, 0, 1))
            eps[:, i] = np.moveaxis(e.reshape(9, e_n, Q), 0, -1)
        tr = eps[..., 0] + eps[..., 4] + eps[..., 8]
        sig = 2.0 * mu * eps
        for d in (0, 4, 8):
            sig[..., d] += lam * tr
        sig *= dx[sl][:, None, :, None]
        # K_e[i, j] = Σ_q σ_i:ε_j — пакетное произведение матриц по элементам (BLAS)
        ke = np.matmul(sig.reshape(e_n, n_loc, Q * 9), eps.reshape(e_n, n_loc, Q * 9).swapaxes(1, 2))
        d_e = dofs[:, sl].T
        rows.append(np.broadcast_to(d_e[:, :, None], ke.shape).ravel())
        cols.append(np.broadcast_to(d_e[:, None, :], ke.shape).ravel())
        vals.append(ke.ravel())
    rows, cols, vals = np.concatenate(rows), np.concatenate(cols), np.concatenate(vals)
    return sp.coo_matrix((vals, (rows, cols)), shape=(basis.N, basis.N)).tocsr()


def _strain_field(basis, u: np.ndarray) -> tuple[float, tuple[float, float, float], float]:
    """Наибольшая по модулю главная деформация, ГДЕ она, и 99-й процентиль.

    Считается в точках интегрирования по всему телу: именно там деформация и
    определена, а узловые значения пришлось бы усреднять, сглаживая как раз
    тот пик, ради которого всё и меряется.

    Место пика нужно, чтобы отличить физику от свойства модели: пик в остром
    внутреннем углу или на кромке заделки растёт при измельчении сетки без
    предела. Процентиль к этому почти нечувствителен — он говорит, что
    происходит в теле, а не в одной точке.
    """
    from skfem.helpers import sym_grad

    field = basis.interpolate(u)
    eps = sym_grad(field)                      # 3×3×элементы×точки
    tensors = np.moveaxis(eps, [0, 1], [-2, -1])
    magnitude = np.abs(np.linalg.eigvalsh(tensors)).max(axis=-1)   # элементы×точки
    where = np.unravel_index(int(np.argmax(magnitude)), magnitude.shape)
    points = basis.mapping.F(basis.X)          # 3×элементы×точки
    at = tuple(float(points[k][where]) for k in range(3))
    return float(magnitude[where]), at, float(np.percentile(magnitude, 99))



def spd_factor(A):
    """Факторизация симметричной положительно определённой матрицы — SuperLU в
    СИММЕТРИЧНОМ режиме (упорядочение по A+Aᵀ, без выбора ведущего вне диагонали).

    Умолчание scipy (COLAMD, как для несимметричной) на матрице жёсткости P2 в
    68 тыс. степеней свободы считало 12.7 с, симметричный режим — 5.9 с при
    расхождении 2e-11. Возвращает функцию решения: второй правой части
    (тест заплаты) факторизация уже не нужна — 0.045 с вместо полного решения.

    Если установлен `pypardiso` (PARDISO из Intel MKL, все ядра), берётся он: на
    плече руки (172 тыс. степеней свободы) 1.3 с против 12.5, а систему в 520 тыс.
    SuperLU не решил вовсе (10 ГБ мало), PARDISO — за 21 с. Он в наборе `mesh` (решение
    владельца), но колёса MKL есть только под x86-64 Linux и Windows — там, где их
    нет, остаётся SuperLU (замеры и выбор — docs/gpu.md, «Решатель МКЭ»). GPU (CuPy,
    cuDSS) и pyamg на наших задачах проиграли.
    """
    from scipy.sparse.linalg import splu

    pardiso = _pardiso()
    if pardiso is not None:
        import scipy.sparse as sp

        upper = sp.triu(A, format="csr")
        upper.sort_indices()
        return _PardisoFactor(pardiso, upper)
    lu = splu(A.tocsc(), permc_spec="MMD_AT_PLUS_A", diag_pivot_thresh=0.0,
              options={"SymmetricMode": True})
    return lu.solve


class _PardisoFactor:
    """Разложение PARDISO как функция решения. Память MKL освобождается ЯВНО, когда
    функция больше не нужна: сборщик мусора Python о ней не знает."""

    def __init__(self, pardiso, upper) -> None:
        self.upper = upper
        self.solver = pardiso.PyPardisoSolver(mtype=2)   # вещественная симметричная положительно определённая
        self.solver.factorize(upper)

    def __call__(self, b):
        return self.solver.solve(self.upper, np.asarray(b, dtype=np.float64))

    def __del__(self) -> None:
        try:
            self.solver.free_memory(everything=True)
        except Exception:                                # noqa: BLE001 — при выходе интерпретатора
            pass


def _pardiso():
    """pypardiso, если он есть и рабочий (MKL нашлась), иначе None."""
    try:
        import pypardiso
    except Exception:                                    # noqa: BLE001 — нет пакета или MKL
        return None
    return pypardiso


def solve_condensed(K, f, *, D=None, x=None) -> np.ndarray:
    """Решить K·u = f с закреплёнными степенями свободы D (значения — из x)."""
    from skfem import condense

    A, b, xfull, inner = condense(K, f, x=x, D=D)
    out = np.array(xfull, dtype=float, copy=True)
    out[inner] = spd_factor(A)(b)
    return out


def _solve(basis, K, f, D) -> np.ndarray:
    return solve_condensed(K, f, D=np.unique(np.asarray(D, dtype=np.int64)))


def _node_at(basis, point: tuple[float, float, float], component: int) -> np.ndarray:
    """Индекс одной степени свободы в названной точке.

    Нужен, чтобы убрать жёсткое смещение, не навязывая лишнего. Через
    `get_dofs` так нельзя: он отбирает ГРАНИ, удовлетворяющие условию, и на
    точечное условие отвечает пустотой — молча, и система становится
    вырожденной.
    """
    loc = basis.doflocs
    mask = np.ones(loc.shape[1], dtype=bool)
    for axis, value in enumerate(point):
        mask &= np.isclose(loc[axis], value)
    mask &= (np.arange(loc.shape[1]) % 3) == component
    found = np.where(mask)[0]
    if len(found) != 1:
        raise FemError(f"в точке {point} компонента {component}: узлов найдено {len(found)}, "
                       f"нужен ровно один")
    return found


def _finish(basis, u, divisions, t0) -> Solution:
    peak, at, p99 = _strain_field(basis, u)
    return Solution(u=u, doflocs=basis.doflocs, dofs=int(basis.N), divisions=divisions,
                    ms=(time.perf_counter() - t0) * 1000,
                    peak_strain=peak, peak_at=at, strain_p99=p99)


# ------------------------------------------------------------------ задачи
def tension(bar: Bar, material: Material, *, stress: float,
            divisions: Divisions = (4, 2, 2)) -> Solution:
    """Одноосное растяжение торца напряжением `stress` (МПа).

    Точное решение ЛИНЕЙНО по координатам, поэтому его обязан воспроизводить
    любой элемент с точностью округления. Это самая надёжная проверка самой
    машинерии: сборка, единицы, закрепления, решатель.
    """
    from skfem import LinearForm, asm

    t0 = time.perf_counter()
    mesh, basis = _pieces(bar, divisions)
    K = _stiffness(basis, material)

    fb = _end_facets(mesh, basis, bar.length)

    @LinearForm
    def pull(v, w):
        return stress * v[0]

    f = asm(pull, fb)
    # Торец x=0 не даёт двигаться вдоль X, плоскости симметрии — поперёк.
    # Поперечное сужение при этом НЕ запрещено: точное решение ему и отвечает.
    D = np.concatenate([
        basis.get_dofs(lambda x: np.isclose(x[0], 0.0)).all("u^1"),
        basis.get_dofs(lambda x: np.isclose(x[1], 0.0)).all("u^2"),
        basis.get_dofs(lambda x: np.isclose(x[2], 0.0)).all("u^3"),
    ])
    return _finish(basis, _solve(basis, K, f, D), divisions, t0)


def pure_bending(bar: Bar, material: Material, *, moment: float,
                 divisions: Divisions = (4, 2, 2)) -> Solution:
    """Чистый изгиб моментом `moment` (Н·мм), приложенным к торцу x=L.

    Момент задаётся распределением σ_xx = M·z/I по торцу — так, как он и
    возникает в балке. Точное трёхмерное решение здесь КВАДРАТИЧНО и в
    точности совпадает с балочным: прогиб M·L²/(2EI), кривизна M/(EI).
    Поэтому P2 обязан дать его точно, и это второй рубеж самопроверки.

    Закрепления подобраны согласованными с точным решением: u_x=0 на торце
    x=0 (там точное u_x и есть ноль при любых y, z), u_y=0 на плоскости
    симметрии y=0, и один узел в начале координат закреплён по z, чтобы убрать
    жёсткое смещение. Никаких лишних связей: полностью защемлённый торец
    сделал бы задачу жёстче балочной, и совпадение перестало бы быть точным.
    """
    from skfem import LinearForm, asm

    t0 = time.perf_counter()
    mesh, basis = _pieces(bar, divisions)
    K = _stiffness(basis, material)
    inertia = bar.inertia

    fb = _end_facets(mesh, basis, bar.length)

    @LinearForm
    def bend(v, w):
        return (moment / inertia) * w.x[2] * v[0]

    f = asm(bend, fb)
    D = np.concatenate([
        basis.get_dofs(lambda x: np.isclose(x[0], 0.0)).all("u^1"),
        basis.get_dofs(lambda x: np.isclose(x[1], 0.0)).all("u^2"),
        _node_at(basis, (0.0, 0.0, 0.0), 2),
    ])
    return _finish(basis, _solve(basis, K, f, D), divisions, t0)


def tip_load(bar: Bar, material: Material, *, force: float,
             divisions: Divisions = (12, 4, 4)) -> Solution:
    """Консоль: торец x=0 защемлён, на торец x=L давит сила `force` вдоль −Z.

    ЭТУ ЗАДАЧУ НЕЛЬЗЯ СРАВНИВАТЬ С ФОРМУЛОЙ P·L³/(3EI) И ЖДАТЬ СОВПАДЕНИЯ.
    Замерено: сходится к значению примерно на 2.5 % НИЖЕ формулы, и разрыв не
    уходит с измельчением сетки. Причина не в сетке, а в постановке: полностью
    защемлённая трёхмерная грань запрещает поперечное сужение и потому жёстче
    балочной заделки, а сама балочная формула пренебрегает сдвигом. Это
    рабочая задача, а не эталон; эталоны — `tension` и `pure_bending`.
    """
    from skfem import LinearForm, asm

    t0 = time.perf_counter()
    mesh, basis = _pieces(bar, divisions)
    K = _stiffness(basis, material)
    traction = -force / bar.area

    fb = _end_facets(mesh, basis, bar.length)

    @LinearForm
    def push(v, w):
        return traction * v[2]

    f = asm(push, fb)
    D = basis.get_dofs(lambda x: np.isclose(x[0], 0.0)).flatten()
    return _finish(basis, _solve(basis, K, f, D), divisions, t0)


def guided_deflection(bar: Bar, material: Material, *, force: float,
                      divisions: Divisions = (12, 4, 2)) -> Solution:
    """Направляемая балка: торец смещается, но НЕ ПОВОРАЧИВАЕТСЯ.

    Так работает флексура-параллелограмм: подвижный блок держат два листа, и
    он остаётся параллелен себе. Балочная формула для этого случая даёт
    жёсткость 12·E·I/L³ — вчетверо больше консольной, потому что направляемая
    балка ведёт себя как две консоли длиной L/2, сложенные встык.

    Считать консоль и умножать на четыре было бы удобно и неверно: поправка
    на ширину листа (стеснённая поперечная кривизна) зависит от того, как
    распределена кривизна по длине, а у направляемой балки она распределена
    иначе. Поэтому задача решается своя.

    Непроворот торца задаётся условием u_x = 0 по всей грани x=L: поворот
    сечения сдвинул бы её точки вдоль X пропорционально z, а это запрещено.
    """
    from skfem import LinearForm, asm

    t0 = time.perf_counter()
    mesh, basis = _pieces(bar, divisions)
    K = _stiffness(basis, material)
    traction = -force / bar.area
    fb = _end_facets(mesh, basis, bar.length)

    @LinearForm
    def push(v, w):
        return traction * v[2]

    f = asm(push, fb)
    D = np.concatenate([
        basis.get_dofs(lambda x: np.isclose(x[0], 0.0)).flatten(),
        basis.get_dofs(lambda x: np.isclose(x[0], bar.length)).all("u^1"),
    ])
    return _finish(basis, _solve(basis, K, f, D), divisions, t0)


#: Во сколько конвертов мешера берётся допуск отбора грани.
#:
#: ПОЧЕМУ НЕ ДОЛЯ ГАБАРИТА, как было сначала. Мешер вправе двигать поверхность
#: внутри своего конверта, и узлы плоской грани ложатся НЕ РОВНО на плоскость:
#: замерено смещение до 5.5·10⁻³ мм на бруске 40×10×2. Прежний допуск в
#: 10⁻⁶ габарита (1.8·10⁻⁴ мм) отбирал тогда 7 граней из 10 — треть заделки
#: оставалась незащемлённой, и прогиб выскакивал на 10 % выше. Поймал это
#: мигающий тест, и слава богу.
FACE_ENVELOPES = 2.0

_AXES = {"x": 0, "y": 1, "z": 2}


def _face(spec: str) -> tuple[int, bool]:
    """Разобрать «z_min» или «x_max» в (ось, брать ли максимум)."""
    parts = str(spec).lower().split("_")
    if len(parts) != 2 or parts[0] not in _AXES or parts[1] not in ("min", "max"):
        raise FemError(f"грань задаётся как «ось_min» или «ось_max» "
                       f"(например z_min), получено {spec!r}")
    return _AXES[parts[0]], parts[1] == "max"


def _region(box, what: str):
    if box is None:
        return None
    values = [float(v) for v in box]
    if len(values) != 6 or any(values[k] >= values[k + 3] for k in range(3)):
        raise FemError(f"{what}: участок задаётся коробкой [x0, y0, z0, x1, y1, z1] "
                       f"с x0 < x1, y0 < y1, z0 < z1; получено {box!r}")
    return np.array(values[:3]), np.array(values[3:])


def solve_faces(tetmesh, material: Material, *, fixed: str, load: str,
                force: tuple[float, float, float],
                tolerance: float | None = None,
                fixed_region=None, load_region=None) -> Solution:
    """Решить задачу на ПРОИЗВОЛЬНОМ теле: одна грань защемлена, другая нагружена.

    Грани задаются крайними: «z_min» — всё, что лежит у наименьшего z. Этого
    хватает подавляющему большинству случаев («закрепить основание, надавить
    сверху»), а произвольный отбор граней потребовал бы своего языка, и
    придумывать его под несуществующего пользователя не стоит.

    ВАЖНО: у крайней грани может не быть плоскости. `tolerance` — толщина
    слоя, в котором узлы считаются принадлежащими грани; по умолчанию она
    ничтожна, и у скруглённого основания в отбор попадёт почти ничего.
    Поэтому число отобранных узлов и площадь грани возвращаются в решении:
    молча закрепить три узла вместо грани — верный способ получить красивую
    чушь.

    `fixed_region`, `load_region` — коробка [x0, y0, z0, x1, y1, z1], которой
    отбор ограничивается: пятно под винтом, участок контакта. Сила,
    размазанная по всей грани, там, где она давит на пятачок, даёт другое
    напряжённое состояние — в методике конструктора это нужно «примерно в
    каждой второй симуляции».
    """
    from skfem import Basis, FacetBasis, LinearForm, asm

    t0 = time.perf_counter()
    mesh = tetmesh.skfem()
    basis = Basis(mesh, _element())
    K = _stiffness(basis, material)

    envelope = getattr(tetmesh, "envelope_mm", 0.0)
    tol = (float(tolerance) if tolerance is not None
           else max(FACE_ENVELOPES * envelope, 1e-6))

    def facets_of(spec: str, region, what: str):
        axis, take_max = _face(spec)
        coordinate = (tetmesh.points[:, axis].max() if take_max
                      else tetmesh.points[:, axis].min())
        bounds = _region(region, what)

        def test(x):
            ok = np.abs(x[axis] - coordinate) <= tol
            if bounds is not None:
                lo, hi = bounds
                for k in range(3):
                    if k != axis:
                        ok &= (x[k] >= lo[k]) & (x[k] <= hi[k])
            return ok

        found = mesh.facets_satisfying(test)
        if len(found) == 0:
            raise FemError(
                f"на грани {spec}{' в участке ' + str(list(region)) if region is not None else ''} "
                f"не нашлось ни одной грани сетки при допуске {tol:g} мм. Либо тело "
                f"там не плоское, либо допуск мал — задайте `tolerance` явно, — либо "
                f"участок мимо грани")
        return found, axis, coordinate

    if _face(fixed) == _face(load) and fixed_region is None and load_region is None:
        raise FemError(f"защемлена и нагружена одна и та же грань {fixed}: сила "
                       f"уйдёт прямо в заделку, и смещение выйдет нулём. Разные "
                       f"участки одной грани задаются fixed_region и load_region")
    hold, _, _ = facets_of(fixed, fixed_region, "закрепление")
    push, _, _ = facets_of(load, load_region, "нагрузка")
    if np.intersect1d(hold, push).size:
        raise FemError(f"защемлённый и нагруженный участки перекрываются ({fixed} и {load}): "
                       f"сила уйдёт прямо в заделку, и смещение выйдет нулём")

    fb = FacetBasis(mesh, basis.elem, facets=push)
    hold_area = float(FacetBasis(mesh, basis.elem, facets=hold).dx.sum())
    # Площадь — СУММА МЕР ИНТЕГРИРОВАНИЯ, а не сборка формы. Первая версия
    # собирала линейную форму «1.0», которая от пробной функции не зависит
    # вовсе: получалось число степеней свободы, умноженное на площадь, и
    # прогиб выходил ровно в десять раз меньше должного.
    area = float(fb.dx.sum())
    if area <= 0:
        raise FemError(f"площадь нагруженной грани {load} вышла нулевой")
    traction = tuple(component / area for component in force)

    @LinearForm
    def applied(v, w):
        return traction[0] * v[0] + traction[1] * v[1] + traction[2] * v[2]

    f = asm(applied, fb)
    D = Basis(mesh, basis.elem).get_dofs(hold).flatten()
    if len(D) == 0:
        raise FemError(f"на защемлённой грани {fixed} не нашлось степеней свободы")

    u = _solve(basis, K, f, D)
    solution = _finish(basis, u, (tetmesh.nodes, tetmesh.cells, 0), t0)
    # Отбор граней виден в ответе: частичное защемление — тихая беда, от
    # которой число выглядит правдоподобным и врёт на десяток процентов.
    return replace(solution, fixed_facets=len(hold), load_facets=len(push),
                   fixed_area=hold_area, load_area=area, face_tolerance=tol)
