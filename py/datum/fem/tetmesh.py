"""Тетраэдральная сетка произвольного тела — претензия №8, вторая часть.

До сих пор решатель брал только призматический брус: сетку ему строить было
нечем, а считать по плохой сетке хуже, чем отказать. Теперь сетку строит
fTetWild — он не подгоняет узлы к поверхности, а перестраивает тело внутри
заданного «конверта», и потому переваривает и органику, и чужие меши с
дефектами.

ПОЧЕМУ БИБЛИОТЕКА ГРУЗИТСЯ ПО ПУТИ, А НЕ ИМПОРТОМ. Пакет `pytetwild` в своём
`__init__` импортирует `pyvista`, а тот тянет VTK — 639 МБ, от которых проект
сознательно отказался ещё в плане. Самому мешеру VTK не нужен: это отдельная
скомпилированная библиотека, принимающая и отдающая массивы numpy. Поэтому она
загружается напрямую, а `pyvista` не ставится вовсе. Приём хрупкий, и потому
отказ говорит, что именно сломалось, а не «нет модуля».

МЕШЕР СОРИТ, И ЗА НИМ УБИРАЮТ. При каждом построении fTetWild пишет в ТЕКУЩИЙ
каталог файл `__tracked_surface.stl` — отладочный след, который никак не
выключается параметрами. Библиотека, гадящая в чужой рабочий каталог, — это не
то, что стоит оставлять: файл удаляется после работы, и только если его там не
было до неё.

МЕШЕР НЕ ДЕТЕРМИНИРОВАН, и знать это важнее, чем удобно. На одном и том же
теле он даёт разные сетки от запуска к запуску — замерено: 199…215 узлов на
бруске 40×10×2, причём и в один поток тоже. Зерна случайности наружу он не
отдаёт, так что поправить это параметрами нельзя.

На ОТВЕТ это влияет меньше, чем можно испугаться: разброс прогиба консоли
0.58 % при грубой сетке и 0.15 % при вдвое мельче, то есть он УБЫВАЕТ вместе с
ошибкой дискретизации и целиком лежит внутри вилки, которую проверка и так
сообщает. Но два следствия стоит назвать. Первое: числовые пороги в тестах
обязаны иметь запас на этот разброс, иначе они будут мигать. Второе: картинка
и сетка, посчитанные заново, получат другой отпечаток, и контент-адресуемый
кеш сохранит новый объект вместо старого — на размер кеша это влияет, на
верность нет. Понадобится строгая воспроизводимость — сетку придётся класть в
кеш по отпечатку тела, а не строить заново.

КАЧЕСТВО СЕТКИ МЕРИТСЯ, А НЕ ПОДРАЗУМЕВАЕТСЯ. У каждого тетраэдра считается
безразмерная мера правильности (1 у правильного тетраэдра, 0 у вырожденного),
и отчёт называет худшую. Вырожденные тетраэдры портят не столько точность,
сколько обусловленность: решение может уехать молча.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import pathlib
import time
from dataclasses import dataclass, field

import numpy as np

from datum.fem.model import FemError
from datum.geom.solid import Solid

__all__ = ["TetMesh", "tet_mesh", "mesher_available"]

#: Длина ребра по умолчанию — доля диагонали габарита. Значение fTetWild.
DEFAULT_EDGE_RELATIVE = 0.05

#: Отладочный след, который fTetWild пишет в текущий каталог. Параметрами не
#: выключается, поэтому мешер зовётся из временного каталога.
LITTER = "__tracked_surface.stl"

#: Толщина «конверта», внутри которого поверхность можно двигать, — тоже доля
#: диагонали. Чем меньше, тем точнее форма и тем больше тетраэдров.
DEFAULT_EPSILON = 1e-3

_WRAPPER = None


def _wrapper():
    """Загрузить библиотеку мешера, минуя `pytetwild.__init__` с его pyvista."""
    global _WRAPPER
    if _WRAPPER is not None:
        return _WRAPPER
    try:
        import pytetwild  # noqa: F401
    except ImportError:
        pass                      # ожидаемо: __init__ спотыкается о pyvista
    except Exception:
        pass
    spec = importlib.util.find_spec("pytetwild")
    if spec is None or not spec.submodule_search_locations:
        raise FemError(
            "мешер fTetWild не установлен: нет пакета pytetwild. "
            "Поставьте `pip install pytetwild` — pyvista при этом НЕ нужен, "
            "библиотека берётся из пакета напрямую")
    module_spec = importlib.machinery.PathFinder.find_spec(
        "PyfTetWildWrapper", spec.submodule_search_locations)
    if module_spec is None or not isinstance(module_spec.loader,
                                             importlib.machinery.ExtensionFileLoader):
        raise FemError("в пакете pytetwild нет нативной библиотеки PyfTetWildWrapper "
                       "для этого интерпретатора")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    if not hasattr(module, "tetrahedralize_mesh"):
        raise FemError("в библиотеке fTetWild нет tetrahedralize_mesh")
    _WRAPPER = module
    return module


def mesher_available() -> bool:
    """Есть ли мешер. Нужно тестам и отчётам, чтобы не гадать по исключению."""
    try:
        _wrapper()
    except FemError:
        return False
    return True


@dataclass(frozen=True, slots=True)
class TetMesh:
    """Сетка и то, что о ней известно."""

    points: np.ndarray = field(repr=False)
    tets: np.ndarray = field(repr=False)
    ms: float = 0.0
    edge_relative: float = 0.0
    #: Толщина «конверта», внутри которого мешер двигал поверхность, — ДОЛЯ
    #: диагонали габарита. Нужна не для отчёта: по ней считается допуск отбора
    #: граней, иначе часть защемляемой грани в отбор не попадёт.
    epsilon: float = DEFAULT_EPSILON
    #: Безразмерная правильность худшего тетраэдра: 1 — правильный, 0 — плоский.
    worst_quality: float = 0.0
    median_quality: float = 0.0
    volume: float = 0.0

    @property
    def diagonal(self) -> float:
        """Диагональ габарита — к ней привязан конверт мешера."""
        lo, hi = self.points.min(axis=0), self.points.max(axis=0)
        return float(np.linalg.norm(hi - lo))

    @property
    def envelope_mm(self) -> float:
        """Конверт в миллиметрах: на столько мешер вправе сдвинуть поверхность."""
        return self.epsilon * self.diagonal

    @property
    def nodes(self) -> int:
        return int(self.points.shape[0])

    @property
    def cells(self) -> int:
        return int(self.tets.shape[0])

    def skfem(self):
        """Сетка в виде, понятном scikit-fem."""
        from skfem import MeshTet

        return MeshTet(np.ascontiguousarray(self.points.T),
                       np.ascontiguousarray(self.tets.T.astype(np.int64)))

    def line(self) -> str:
        return (f"{self.nodes} узлов, {self.cells} тетраэдров, объём "
                f"{self.volume:.2f} мм³, правильность худшего "
                f"{self.worst_quality:.3f} (медиана {self.median_quality:.3f}), "
                f"{self.ms:.0f} мс")


def _quality(points: np.ndarray, tets: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Безразмерная правильность и объём каждого тетраэдра.

    q = 12·(3V)^(2/3) / Σ l², равна единице у правильного тетраэдра и нулю у
    вырожденного. Мера стандартная и считается на массивах, без циклов.
    """
    p = points[tets]                                   # (N, 4, 3)
    a, b, c, d = p[:, 0], p[:, 1], p[:, 2], p[:, 3]
    volume = np.abs(np.einsum("ij,ij->i", b - a, np.cross(c - a, d - a))) / 6.0
    edges = np.stack([b - a, c - a, d - a, c - b, d - b, d - c], axis=1)
    sum_sq = np.einsum("ijk,ijk->i", edges, edges)
    with np.errstate(divide="ignore", invalid="ignore"):
        q = 12.0 * np.cbrt((3.0 * volume) ** 2) / sum_sq
    return np.nan_to_num(q, nan=0.0, posinf=0.0), volume


def tet_mesh(solid: Solid, *, edge_relative: float = DEFAULT_EDGE_RELATIVE,
             epsilon: float = DEFAULT_EPSILON, threads: int = 4) -> TetMesh:
    """Построить тетраэдральную сетку тела.

    `edge_relative` — целевая длина ребра как доля диагонали габарита. Это
    главный рычаг «точность против времени»: у бруска 40×10×2 доля 0.05 даёт
    209 узлов за 0.3 с, 0.04 — 333 узла.

    `epsilon` — толщина конверта, внутри которого поверхность можно двигать,
    тоже долей диагонали. Мешер НЕ обязан сохранять исходные вершины: он
    перестраивает тело, оставаясь в конверте. Отсюда и его всеядность, и то,
    что плоскости симметрии исходного тела в сетке узлами не лежат — условия
    на них ставить нельзя.
    """
    from datum.geom import man
    from datum.geom.convert import vertices_faces

    if edge_relative <= 0 or epsilon <= 0:
        raise FemError(f"длина ребра и конверт должны быть положительными, "
                       f"получено {edge_relative} и {epsilon}")
    vertices, faces = vertices_faces(man(solid))
    if len(vertices) == 0:
        raise FemError("тело пусто: строить сетку не по чему")
    points, tets, ms = _tetrahedralize(np.ascontiguousarray(vertices, dtype=np.float64),
                                       np.ascontiguousarray(faces, dtype=np.uint32),
                                       float(edge_relative), float(epsilon), int(threads))
    return _finish_mesh(points, tets, ms, edge_relative, epsilon)


def _tetrahedralize(vertices: np.ndarray, faces: np.ndarray, edge_relative: float,
                    epsilon: float, threads: int):
    """Сам вызов мешера — над массивами, чтобы его можно было отдать чистому процессу."""
    wrapper = _wrapper()

    # Мешер пишет отладочный след в ТЕКУЩИЙ каталог, и параметрами это не
    # выключается. Прежде след убирался после — но только если файла не было до
    # вызова, а значит, раз попав в каталог (падение посреди работы, прерванный
    # тест), он оставался там навсегда и перезаписывался каждым построением.
    # Теперь мешер работает во временном каталоге, и следу негде остаться.
    # (Смена каталога — на весь процесс: два мешера в потоках одного процесса
    # роняли друг другу каталог. Параллельно — только процессами, `tet_meshes`.)
    import os
    import tempfile

    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="datum-tetwild-") as scratch:
        previous = os.getcwd()
        os.chdir(scratch)
        try:
            points, tets = wrapper.tetrahedralize_mesh(
                vertices,
                faces,
                True,            # optimize
                False,           # skip_simplify
                float(edge_relative),
                -1.0,            # edge_length_abs — не задаём, идём от доли
                float(epsilon),
                10.0,            # stop_energy
                True,            # coarsen
                int(threads),
                2,               # num_opt_iter
                4,               # loglevel: только ошибки
                True,            # quiet
                False,           # vtk_ordering — нам нужен обычный порядок
                False,           # disable_filtering
                np.zeros((0, 3)), np.zeros((0, 4), dtype=np.int32), np.zeros(0))
        finally:
            os.chdir(previous)
    return points, tets, (time.perf_counter() - t0) * 1000


def _finish_mesh(points, tets, ms, edge_relative, epsilon) -> TetMesh:
    if len(tets) == 0:
        raise FemError("мешер вернул пустую сетку: тело слишком тонкое для "
                       f"длины ребра {edge_relative} или не замкнуто")
    q, volume = _quality(points, tets)
    return TetMesh(points=points, tets=tets, ms=ms, edge_relative=edge_relative,
                   epsilon=float(epsilon),
                   worst_quality=float(q.min()), median_quality=float(np.median(q)),
                   volume=float(volume.sum()))


def tet_meshes(solid: Solid | None, edges: list[float], *, epsilon: float = DEFAULT_EPSILON,
               threads: int = 4, batch: list | None = None, jobs: int | None = None):
    """Несколько сеток (лестница сеток одного тела или `batch` — [(тело, [рёбра])…])
    ОДНОВРЕМЕННО, в отдельных процессах; не больше `jobs` сразу (ядра ÷ потоки).

    fTetWild сам по ядрам почти не делится: плечо руки — 44 с на 4 потоках и 41 с на
    16. Поэтому все ядра занимают разные сетки разом. Не multiprocessing: `fork`
    после первого вызова мешера зависал навсегда на 0 % ЦП (пулы потоков родителя),
    а `spawn` заново исполняет главный модуль родителя (сервер, сценарий). Поэтому
    — своя команда (`datum.fem._mesh_child`) с обменом через временные файлы.

    Ответ: список сеток (для `solid`) или список списков (для `batch`)."""
    import os
    import subprocess
    import sys
    import tempfile

    from datum.geom import man
    from datum.geom.convert import vertices_faces

    groups = batch if batch is not None else [(solid, edges)]
    arrays = []
    for body, es in groups:
        for e in es:
            if e <= 0:
                raise FemError(f"длина ребра должна быть положительной, получено {e}")
        vertices, faces = vertices_faces(man(body))
        if len(vertices) == 0:
            raise FemError("тело пусто: строить сетку не по чему")
        arrays.append((np.ascontiguousarray(vertices, dtype=np.float64),
                       np.ascontiguousarray(faces, dtype=np.uint32)))
    tasks = [(g, k, e) for g, (_, es) in enumerate(groups) for k, e in enumerate(es)]
    if len(tasks) == 1:
        g, _, e = tasks[0]
        v, f = arrays[g]
        return ([_finish_mesh(*_tetrahedralize(v, f, float(e), float(epsilon), threads),
                              e, epsilon)] if batch is None else
                [[_finish_mesh(*_tetrahedralize(v, f, float(e), float(epsilon), threads),
                               e, epsilon)]])
    limit = max(1, jobs or (os.cpu_count() or 4) // max(threads, 1))
    out: dict[tuple[int, int], TetMesh] = {}
    with tempfile.TemporaryDirectory(prefix="datum-ladder-") as tmp:
        for g, (v, f) in enumerate(arrays):
            np.savez(os.path.join(tmp, f"in{g}.npz"), v=v, f=f)

        def start(g, k, e):
            dst = os.path.join(tmp, f"out{g}_{k}.npz")
            cmd = [sys.executable, "-m", "datum.fem._mesh_child", os.path.join(tmp, f"in{g}.npz"),
                   dst, repr(float(e)), repr(float(epsilon)), str(int(threads))]
            # stdout — в трубу, не в /dev/null: перила изолированной операции запрещают
            # открывать на запись что-либо вне проекта, и /dev/null тоже.
            return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    cwd=tmp), dst

        queue = list(tasks)
        running: list = []
        try:
            while queue or running:
                while queue and len(running) < limit:
                    g, k, e = queue.pop(0)
                    running.append((*start(g, k, e), g, k, e, False))
                proc, dst, g, k, e, retried = running[0]
                _, err = proc.communicate()
                running.pop(0)
                if proc.returncode != 0 and not retried:
                    # fTetWild не детерминирован и изредка падает сам (SIGFPE на плече
                    # руки один раз из нескольких, на сетке, что потом строилась) — повтор.
                    running.append((*start(g, k, e), g, k, e, True))
                    continue
                if proc.returncode != 0:
                    tail = err.decode("utf-8", "replace").strip().splitlines()[-3:]
                    raise FemError(f"мешер (ребро {e:g}) упал с кодом {proc.returncode}: "
                                   + " | ".join(tail))
                with np.load(dst) as result:
                    out[(g, k)] = _finish_mesh(result["points"], result["tets"],
                                               float(result["ms"]), e, epsilon)
        finally:
            for proc, *_ in running:
                proc.kill()
            for proc, *_ in running:
                proc.communicate()
    result = [[out[(g, k)] for k in range(len(es))] for g, (_, es) in enumerate(groups)]
    return result[0] if batch is None else result
