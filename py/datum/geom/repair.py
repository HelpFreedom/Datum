"""Диагностика и ремонт чужих мешей.

Модели со стороны (скачанные, отсканированные, выгруженные чужим CAD) приходят
дырявыми, с перевёрнутыми нормалями, дублями и мусором. Datum чинит их лестницей
от дешёвого к дорогому и ВЕДЁТ ЖУРНАЛ: видно, что именно сделали с чужой
геометрией, и ремонт можно переиграть.

Лестница:
  1. чистка      — бесконечные значения, слияние вершин, вырожденные и
                   повторяющиеся грани, висячие вершины;
  2. ориентация  — намотка, нормали, вывернутость;
  3. заделка дыр — trimesh.repair.fill_holes;
  4. тела        — оставить крупнейшее (по желанию);
  5. ПЕРЕСТРОЙКА — воксельная, последний довод: геометрия приносится в жертву
                   ради гарантированной замкнутости.

О последней ступени — она написана по результатам замеров, а не по наитию:

* `mesh.contains()` на дырявом меше ошибается: на сфере с выкушенными 10 %
  граней он неверно классифицировал 5 % точек — лучи утекают через дыры.
  Поэтому SDF строится НЕ лучевым тестом, а растеризацией треугольников в
  воксели.
* Голой заливки мало. Замер: при шаге 1.0 мм дыры мельче вокселя и заливка даёт
  120 % объёма шара, а при 0.5 и 0.25 мм она утекает наружу и остаётся одна
  скорлупа — 19 % и 9 %. То есть чем мельче сетка, тем хуже, что
  противоестественно.
* Лечится морфологическим замыканием: расширить на k вокселей, залить, сжать
  обратно. Замер после этого: 99–105 % объёма на обоих шагах.
* Изоповерхность строится `Manifold.level_set`. Он принимает питоновскую SDF, но
  считает разреженно, только около поверхности: 242 тыс. вызовов за 0.09 с. Так
  что зависимость `scikit-image` (её требует `marching_cubes` в trimesh) не нужна.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
import trimesh
from scipy import ndimage

from datum.geom.convert import from_trimesh, to_trimesh
from datum.geom.solid import Solid
from datum.geom.topology import analyze

__all__ = ["rasterize_surface", "Diagnosis", "RepairStep", "RepairResult", "diagnose", "repair",
           "voxel_remesh", "load_mesh", "load_objects", "LoadedObject"]

Level = Literal["auto", "light", "remesh"]


# --------------------------------------------------------------------- диагноз
@dataclass(frozen=True, slots=True)
class Diagnosis:
    triangles: int
    vertices: int
    watertight: bool
    winding_consistent: bool
    volume_mm3: float
    area_mm2: float
    extents_mm: tuple[float, float, float]
    components: int
    bodies: int
    sealed_voids: int
    degenerate_faces: int
    duplicate_faces: int
    unreferenced_vertices: int
    boundary_edges: int
    nonmanifold_edges: int
    units_suspect: bool
    manifold_status: str

    @property
    def healthy(self) -> bool:
        return (self.watertight and self.winding_consistent
                and self.components == 1 and self.degenerate_faces == 0
                and self.duplicate_faces == 0 and self.manifold_status == "NoError")

    def problems(self) -> list[str]:
        out: list[str] = []
        if not self.watertight:
            причины = []
            if self.boundary_edges > 0:
                причины.append(f"открытых рёбер {self.boundary_edges} (дыры)")
            if self.nonmanifold_edges > 0:
                причины.append(f"неманифольдных рёбер {self.nonmanifold_edges} "
                               f"(ребро делят три грани и более)")
            out.append("не замкнут: " + ("; ".join(причины) if причины
                                         else "причина не в рёбрах — проверьте намотку"))
        if not self.winding_consistent:
            out.append("намотка граней несогласованна")
        if self.components > 1:
            out.append(f"несвязных кусков: {self.components}")
        if self.degenerate_faces:
            out.append(f"вырожденных граней: {self.degenerate_faces}")
        if self.duplicate_faces:
            out.append(f"повторяющихся граней: {self.duplicate_faces}")
        if self.unreferenced_vertices:
            out.append(f"висячих вершин: {self.unreferenced_vertices}")
        if self.sealed_voids:
            out.append(f"запаянных полостей: {self.sealed_voids}")
        if self.units_suspect:
            out.append("подозрительный масштаб: габарит меньше 1 мм — "
                       "похоже, файл в метрах или дюймах")
        if self.manifold_status != "NoError":
            out.append(f"ядро не принимает геометрию: {self.manifold_status}")
        return out

    def as_dict(self) -> dict:
        return {
            "triangles": self.triangles, "vertices": self.vertices,
            "watertight": self.watertight, "winding_consistent": self.winding_consistent,
            "volume_mm3": round(self.volume_mm3, 3), "area_mm2": round(self.area_mm2, 3),
            "extents_mm": [round(v, 3) for v in self.extents_mm],
            "components": self.components, "bodies": self.bodies,
            "sealed_voids": self.sealed_voids,
            "degenerate_faces": self.degenerate_faces,
            "duplicate_faces": self.duplicate_faces,
            "unreferenced_vertices": self.unreferenced_vertices,
            "boundary_edges": self.boundary_edges,
            "nonmanifold_edges": self.nonmanifold_edges,
            "units_suspect": self.units_suspect,
            "manifold_status": self.manifold_status,
            "healthy": self.healthy,
            "problems": self.problems(),
        }


def diagnose(mesh: trimesh.Trimesh) -> Diagnosis:
    """Померить меш. Ничего не меняет."""
    try:
        nondeg = int((~mesh.nondegenerate_faces()).sum())
    except Exception:                                   # pragma: no cover
        nondeg = -1
    try:
        dup = int(len(mesh.faces) - mesh.unique_faces().sum())
    except Exception:                                   # pragma: no cover
        dup = -1
    try:
        used = np.unique(mesh.faces)
        unref = int(len(mesh.vertices) - len(used))
    except Exception:                                   # pragma: no cover
        unref = -1

    # Классификация рёбер через trimesh.grouping — на меше в 1.27 млн граней это
    # 0.5 с против 2.1 с у np.unique(axis=0), числа совпадают.
    try:
        from trimesh import grouping

        edges = mesh.edges_sorted
        n_unique = int(len(mesh.edges_unique))
        boundary = int(len(grouping.group_rows(edges, require_count=1)))
        n_manifold = int(len(grouping.group_rows(edges, require_count=2)))
        nonmanifold = max(0, n_unique - boundary - n_manifold)
    except Exception:                                   # pragma: no cover
        boundary = nonmanifold = -1

    rep = analyze(from_trimesh(mesh))
    ext = tuple(float(v) for v in mesh.extents) if len(mesh.faces) else (0.0, 0.0, 0.0)

    return Diagnosis(
        triangles=int(len(mesh.faces)), vertices=int(len(mesh.vertices)),
        watertight=bool(mesh.is_watertight),
        winding_consistent=bool(mesh.is_winding_consistent),
        volume_mm3=float(mesh.volume) if mesh.is_watertight else 0.0,
        area_mm2=float(mesh.area), extents_mm=ext,
        # body_count считает связность ПО ВЕРШИНАМ. Это и осмысленнее, и в 200 раз
        # дешевле: на mech_arm.3mf он даёт 15 тел за 0.07 с, тогда как
        # split(only_watertight=False) — 123 692 «куска» за 13.6 с, потому что
        # смежность по граням рассыпается на неманифольдных рёбрах.
        components=int(mesh.body_count) if len(mesh.faces) else 0,
        bodies=rep.bodies, sealed_voids=rep.sealed_voids,
        degenerate_faces=nondeg, duplicate_faces=dup,
        unreferenced_vertices=unref, boundary_edges=boundary,
        nonmanifold_edges=nonmanifold,
        units_suspect=bool(float(np.linalg.norm(ext)) < 1.0) if any(ext) else False,
        manifold_status=rep.status,
    )


# --------------------------------------------------------------------- журнал
@dataclass(frozen=True, slots=True)
class RepairStep:
    op: str
    note: str
    params: dict = field(default_factory=dict)
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)
    ms: float = 0.0

    def line(self) -> str:
        return f"  {self.op:<16} {self.note}"


@dataclass(frozen=True, slots=True)
class RepairResult:
    solid: Solid
    mesh: trimesh.Trimesh
    before: Diagnosis
    after: Diagnosis
    steps: tuple[RepairStep, ...] = ()

    @property
    def ok(self) -> bool:
        return self.after.watertight and self.after.manifold_status == "NoError"

    def report(self) -> str:
        lines = ["Было:"] + [f"  — {p}" for p in self.before.problems() or ["всё в порядке"]]
        lines.append("Сделано:")
        lines += [s.line() for s in self.steps] or ["  (ничего не потребовалось)"]
        lines.append("Стало:")
        lines += [f"  — {p}" for p in self.after.problems() or ["замкнутый меш, одно тело"]]
        dv = self.after.volume_mm3 - self.before.volume_mm3
        if self.before.volume_mm3 > 0:
            lines.append(f"Объём: {self.before.volume_mm3:.1f} → {self.after.volume_mm3:.1f} мм³ "
                         f"({dv:+.1f}, {100*dv/self.before.volume_mm3:+.1f} %)")
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "before": self.before.as_dict(), "after": self.after.as_dict(),
                "steps": [{"op": s.op, "note": s.note, "params": s.params, "ms": round(s.ms, 1)}
                          for s in self.steps]}


# --------------------------------------------------------------------- ремонт
def load_mesh(path: str | Path) -> trimesh.Trimesh:
    """Прочитать файл как один меш (сцена схлопывается).

    3MF — через lib3mf (`io.read3mf`): trimesh на нём и в 13 раз прожорливее по
    памяти, и неверно собирает файлы Bambu/Orca со ссылками на части пакета.
    И так, как его печатает слайсер (`load_objects`): модификаторы — не
    геометрия, вычитаемые части вычтены.
    """
    if Path(path).suffix.lower() == ".3mf":
        objects, _ = load_objects(path)
        meshes = [o.mesh for o in objects]
        return meshes[0] if len(meshes) == 1 else trimesh.util.concatenate(meshes)
    obj = trimesh.load(str(path), force="mesh")
    if isinstance(obj, trimesh.Scene):                  # pragma: no cover
        obj = trimesh.util.concatenate(tuple(obj.geometry.values()))
    return obj


@dataclass(frozen=True)
class LoadedObject:
    """Объект файла одной сеткой — и что с ним сделано при чтении."""

    name: str
    mesh: trimesh.Trimesh
    notes: tuple[str, ...] = ()


def _as_printed(obj) -> tuple[trimesh.Trimesh, list[str]]:
    """Объект 3MF одной сеткой — как его печатает слайсер.

    Части модели СЛИВАЮТСЯ (слайсер объединяет их при нарезке; склеенные без
    слияния, перекрытия считались бы дважды), вычитаемые части ВЫЧИТАЮТСЯ —
    замерено на ElegooSlicer: со 100 % заполнением пластика меньше ровно на
    вырезанное. Объект из одной части без вычитаемых не трогается вовсе: его
    сетка — та, что в файле, треугольник в треугольник.

    Булева требует замкнутых сеток. Не замкнута хоть одна часть — части
    складываются как есть, и это НЕ молчание: вычитаемое не вычтено, о чём
    сказано в замечаниях.
    """
    from manifold3d import Manifold, OpType

    notes = list(obj.notes)
    parts = [trimesh.Trimesh(p.vertices, p.faces) for p in obj.parts]
    if len(parts) == 1 and not obj.negatives:
        return parts[0], notes
    joined = parts[0] if len(parts) == 1 else trimesh.util.concatenate(parts)
    negatives = [trimesh.Trimesh(n.vertices, n.faces) for n in obj.negatives]

    def ok(man) -> bool:
        return str(man.status()).removeprefix("Error.") == "NoError" and not man.is_empty()

    models = [from_trimesh(m) for m in parts]
    cutters = [from_trimesh(m) for m in negatives]
    if not all(ok(m) for m in models + cutters):
        if cutters:
            notes.append(f"ВЫЧИТАЕМЫЕ ЧАСТИ НЕ ВЫЧТЕНЫ ({len(cutters)}): сетка части не "
                         f"замкнута — вычтите их в скрипте детали или почините в слайсере")
        if len(parts) > 1:
            notes.append(f"части модели ({len(parts)}) не слиты: сетка части не замкнута, "
                         f"перекрытия считаются дважды")
        return joined, notes
    body = models[0] if len(models) == 1 else Manifold.batch_boolean(models, OpType.Add)
    if len(parts) > 1:
        notes.append(f"частей модели {len(parts)} — слиты в одно тело, как при нарезке")
    if cutters:
        cutter = cutters[0] if len(cutters) == 1 else Manifold.batch_boolean(cutters, OpType.Add)
        cut = body - cutter
        if cut.is_empty():
            notes.append("ВНИМАНИЕ: вычитаемые части съели объект целиком — взят без вычитания")
            return to_trimesh(body), notes
        body = cut
        notes.append(f"вычитаемых частей {len(cutters)} — вычтены, как при нарезке")
    return to_trimesh(body), notes


def load_objects(path: str | Path) -> tuple[list[LoadedObject], list[str]]:
    """Файл ПО ОБЪЕКТАМ, в координатах файла, мм, — и замечания о файле.

    Деталь = объект файла (решение владельца): у 3MF это элементы сборки (что
    слайсер показывает отдельно; части одного объекта — вместе), у OBJ/PLY/GLB —
    геометрии сцены, у STL объект один. 3MF читается так, как его печатает
    слайсер (`io.read3mf`, `_as_printed`). Имени может не быть — пустая строка.
    """
    if Path(path).suffix.lower() == ".3mf":
        from datum.io.read3mf import read_3mf_objects

        raw, notes = read_3mf_objects(path)
        objects = []
        for o in raw:
            mesh, own = _as_printed(o)
            objects.append(LoadedObject(o.name, mesh, tuple(own)))
        return objects, notes
    obj = trimesh.load(str(path))
    if isinstance(obj, trimesh.Scene):
        out = []
        for node in obj.graph.nodes_geometry:
            transform, geometry = obj.graph[node]
            mesh = obj.geometry[geometry]
            if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
                continue
            mesh = mesh.copy()
            mesh.apply_transform(transform)
            out.append(LoadedObject(str(node), mesh))
        if out:
            return out, []
    return [LoadedObject("", load_mesh(path))], []


#: Сколько точек выборки держать в памяти за раз. 4 млн — это 96 МБ координат.
RASTER_BUDGET = 4_000_000

#: Предел дробления одной грани. 2800 шагов при шаге выборки треть вокселя —
#: это ребро около метра; для детали, которую печатают, столько не бывает.
RASTER_MAX_STEPS = 2800

#: Шаг выборки по грани, в долях вокселя. Замерено на наклонной грани, худшем
#: случае для решётки: при половине вокселя мимо размеченных проходит 0.94 %
#: точек поверхности (оболочка дырявая, и заливка через такую дыру вытекает),
#: при 0.4 — ни одной из двухсот тысяч. Взята треть, с запасом: это вдвое
#: больше точек, а в абсолютных числах всё равно единицы мегабайт.
RASTER_STEP = 1.0 / 3.0


#: Ячеек решётки вокселей — не больше. Перестройка держит решётку занятости,
#: поле расстояний (float64) и преобразование признаков scipy — около 20 байт
#: на ячейку: 50 млн — ~1 ГБ, в запасе операций над чужим мешем (4 ГБ). Шаг по
#: умолчанию (1/200 диагонали) даёт до ~9 млн.
VOXEL_CELLS_MAX = 50_000_000


def rasterize_surface(mesh: trimesh.Trimesh, pitch: float, *,
                      pad: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """Разметить воксели, через которые проходит поверхность.

    ПОЧЕМУ НЕ `mesh.voxelized()`. По умолчанию trimesh растеризует
    подразбиением: он рекурсивно четвертит ВСЕ грани, пока самая крупная не
    станет мельче вокселя. Цена определяется самой крупной гранью, а платят её
    все. Замерено на настоящей модели с Thingiverse (17 тысяч граней, одна
    грань 4020 мм²): семь четвертований дают 276 миллионов граней и 3.5 ГБ —
    и ремонт не проходил в отдельном процессе, упираясь в предел памяти.

    Здесь каждая грань дробится по СВОЕМУ размеру: точки берутся по
    барицентрической решётке с шагом в треть вокселя (см. RASTER_STEP). Для той
    же модели это около 350 тысяч точек, то есть единицы мегабайт. Лучевой метод не годится по другой
    причине, уже записанной ниже: он врёт на дырявых мешах, а чиним мы именно
    такие.

    Возвращает решётку занятости и мировые координаты ЦЕНТРА вокселя [0,0,0].
    """
    vertices = np.asarray(mesh.vertices, dtype=float)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if len(faces) == 0:                                # pragma: no cover
        raise ValueError("нечего растеризовать: в меше нет граней")

    # Полвокселя сверх отступа — не щедрость, а защита от округления.
    # При ровном `pad * pitch` индекс крайней вершины считается как
    # floor(3.0), а на числах с плавающей точкой это иногда 2.9999… → 2.
    # Отступ выходил на воксель тоньше заявленного, и после расширения на
    # close_k оболочка упиралась в край решётки — заливке негде отличить
    # наружное от внутреннего.
    low = vertices.min(axis=0) - (pad + 0.5) * pitch
    high = vertices.max(axis=0) + (pad + 0.5) * pitch
    if not pitch > 0:
        raise ValueError(f"шаг вокселя {pitch:g} мм — должен быть больше нуля")
    extent = float((vertices.max(axis=0) - vertices.min(axis=0)).max())
    if pitch > extent:
        raise ValueError(f"шаг вокселя {pitch:g} мм крупнее самой модели ({extent:.1f} мм): "
                         f"от неё не останется формы — возьмите шаг мельче; 0 — подобрать самому")
    sizes = (high - low) / pitch
    cells = float(np.prod(sizes + 1))
    if not np.isfinite(cells) or cells > VOXEL_CELLS_MAX:
        # Без предела шаг 0.001 мм на кубике 10 мм просил 934 ГиБ, а шаг 1e308 —
        # переполнение в целое; оба — сырым исключением numpy.
        raise ValueError(
            f"решётка вокселей вышла бы в {cells:.3g} ячеек при пределе "
            f"{VOXEL_CELLS_MAX / 1e6:g} млн (шаг {pitch:g} мм на габарите "
            f"{' × '.join(f'{v:.1f}' for v in vertices.max(axis=0) - vertices.min(axis=0))} мм)"
            f" — возьмите шаг крупнее; 0 — подобрать самому")
    shape = tuple(int(np.ceil(sizes[i])) + 1 for i in range(3))
    occupancy = np.zeros(shape, dtype=bool)
    limit = np.array(shape, dtype=np.int64) - 1

    corner = vertices[faces[:, 0]]
    edge1 = vertices[faces[:, 1]] - corner
    edge2 = vertices[faces[:, 2]] - corner
    # Третье ребро может оказаться длиннее двух первых, поэтому считается тоже.
    longest = np.maximum.reduce([
        np.linalg.norm(edge1, axis=1),
        np.linalg.norm(edge2, axis=1),
        np.linalg.norm(edge2 - edge1, axis=1),
    ])
    steps = np.ceil(longest / (pitch * RASTER_STEP)).astype(np.int64)
    np.clip(steps, 1, RASTER_MAX_STEPS, out=steps)

    for level in np.unique(steps):
        level = int(level)
        rows, cols = np.meshgrid(np.arange(level + 1), np.arange(level + 1),
                                 indexing="ij")
        inside = (rows + cols) <= level
        u = (rows[inside] / level).astype(float)
        v = (cols[inside] / level).astype(float)
        per_face = len(u)
        batch = max(1, RASTER_BUDGET // per_face)

        same = np.flatnonzero(steps == level)
        for start in range(0, len(same), batch):
            index = same[start:start + batch]
            points = (corner[index][:, None, :]
                      + u[None, :, None] * edge1[index][:, None, :]
                      + v[None, :, None] * edge2[index][:, None, :])
            cells = np.floor((points.reshape(-1, 3) - low) / pitch).astype(np.int64)
            np.clip(cells, 0, limit, out=cells)
            occupancy[cells[:, 0], cells[:, 1], cells[:, 2]] = True

    # Центр вокселя [0,0,0]: именно так его понимает интерполяция ниже.
    return occupancy, low + pitch * 0.5


def voxel_remesh(mesh: trimesh.Trimesh, *, pitch: float | None = None,
                 close_k: int = 2, pad: int = 3) -> trimesh.Trimesh:
    """Последний довод: перестроить тело по вокселям.

    Треугольники растеризуются в воксели своей же растеризацией (почему не
    `mesh.voxelized()` — см. `rasterize_surface`; лучевой тест не используется
    по другой причине: он врёт на дырявых мешах, а чиним мы именно такие), дыры
    закрываются морфологическим замыканием, затем по знаковому расстоянию
    строится изоповерхность.

    pitch  — размер вокселя в мм; по умолчанию 1/200 габаритной диагонали,
             но не мельче 0.1 мм;
    close_k— на сколько вокселей расширять перед заливкой. Закрывает дыры
             шириной примерно до 2·close_k вокселей.

    ТЕЛО ВЫХОДИТ КРУПНЕЕ ИСХОДНОГО, и это свойство метода, а не погрешность.
    Изоповерхность строится по границе размеченных вокселей, то есть примерно
    на полвокселя наружу от настоящей поверхности. Замерено на шаре r=10:
    +20 % объёма при шаге 1.0 мм, +10 % при 0.5, +5 % при 0.25 — ровно вдвое с
    шагом. Хотите точнее — берите шаг мельче; поправку на полвокселя здесь не
    вводят намеренно, иначе стенка толщиной в один воксель исчезла бы совсем.
    """
    if pitch is None:
        diag = float(np.linalg.norm(mesh.extents))
        pitch = max(diag / 200.0, 0.1)

    occ, origin = rasterize_surface(mesh, pitch, pad=pad)

    st = ndimage.generate_binary_structure(3, 1)
    if close_k > 0:
        occ = ndimage.binary_dilation(occ, st, iterations=close_k)
    occ = ndimage.binary_fill_holes(occ)
    if close_k > 0:
        occ = ndimage.binary_erosion(occ, st, iterations=close_k)

    if not occ.any():                                   # pragma: no cover
        raise ValueError("после вокселизации не осталось материала — шаг слишком крупный")

    sdf = (ndimage.distance_transform_edt(occ)
           - ndimage.distance_transform_edt(~occ)) * pitch

    nx, ny, nz = (int(v) for v in occ.shape)
    inv = 1.0 / pitch

    def f(x: float, y: float, z: float) -> float:
        fx = (x - origin[0]) * inv
        fy = (y - origin[1]) * inv
        fz = (z - origin[2]) * inv
        i = int(fx); j = int(fy); k = int(fz)
        if i < 0 or j < 0 or k < 0 or i >= nx - 1 or j >= ny - 1 or k >= nz - 1:
            return -1e3
        tx = fx - i; ty = fy - j; tz = fz - k
        c = sdf
        c00 = c[i, j, k] * (1 - tx) + c[i + 1, j, k] * tx
        c01 = c[i, j, k + 1] * (1 - tx) + c[i + 1, j, k + 1] * tx
        c10 = c[i, j + 1, k] * (1 - tx) + c[i + 1, j + 1, k] * tx
        c11 = c[i, j + 1, k + 1] * (1 - tx) + c[i + 1, j + 1, k + 1] * tx
        c0 = c00 * (1 - ty) + c10 * ty
        c1 = c01 * (1 - ty) + c11 * ty
        return float(c0 * (1 - tz) + c1 * tz)

    hi = origin + (np.array(occ.shape) - 1) * pitch
    from manifold3d import Manifold

    man = Manifold.level_set(
        f, [float(origin[0]), float(origin[1]), float(origin[2]),
            float(hi[0]), float(hi[1]), float(hi[2])], pitch)
    return to_trimesh(man)


def repair(mesh: trimesh.Trimesh | str | Path, *, level: Level = "auto",
           keep_largest: bool = True, pitch: float | None = None,
           close_k: int = 2, name: str = "") -> RepairResult:
    """Починить меш по лестнице. Возвращает результат вместе с журналом."""
    if isinstance(mesh, (str, Path)):
        mesh = load_mesh(mesh)
    mesh = mesh.copy()
    before = diagnose(mesh)
    steps: list[RepairStep] = []

    def snap() -> dict:
        return {"triangles": int(len(mesh.faces)),
                "watertight": bool(mesh.is_watertight),
                "components": int(mesh.body_count) if len(mesh.faces) else 0}

    def add(op: str, note: str, t0: float, params: dict | None = None, b: dict | None = None):
        steps.append(RepairStep(op=op, note=note, params=params or {},
                                before=b or {}, after=snap(),
                                ms=(time.perf_counter() - t0) * 1000))

    if level != "remesh":
        # --- 1. чистка ---
        b = snap(); t0 = time.perf_counter()
        mesh.remove_infinite_values()
        mesh.merge_vertices()
        keep = mesh.nondegenerate_faces() & mesh.unique_faces()
        dropped = int((~keep).sum())
        if dropped:
            mesh.update_faces(keep)
        mesh.remove_unreferenced_vertices()
        if dropped or b["triangles"] != len(mesh.faces):
            add("чистка", f"убрано вырожденных и повторяющихся граней: {dropped}",
                t0, {"dropped": dropped}, b)

        # --- 2. ориентация ---
        if not mesh.is_winding_consistent:
            b = snap(); t0 = time.perf_counter()
            trimesh.repair.fix_winding(mesh)
            trimesh.repair.fix_normals(mesh, multibody=True)
            add("ориентация", "согласована намотка граней и направление нормалей", t0, {}, b)

        # --- 3. дыры ---
        if not mesh.is_watertight:
            b = snap(); t0 = time.perf_counter()
            trimesh.repair.fill_holes(mesh)
            note = ("дыры заделаны, меш замкнулся" if mesh.is_watertight
                    else "часть дыр заделана, меш ещё не замкнут")
            add("дыры", note, t0, {}, b)

        # --- 4. лишние куски ---
        if keep_largest and len(mesh.faces) and mesh.body_count > 1:
            parts = mesh.split(only_watertight=False)
            if len(parts) > 1:
                b = snap(); t0 = time.perf_counter()
                biggest = max(parts, key=lambda p: float(p.area))
                dropped_n = len(parts) - 1
                mesh = biggest
                add("тела", f"оставлено крупнейшее тело, отброшено кусков: {dropped_n}",
                    t0, {"dropped": dropped_n}, b)

        # --- 5. вывернутость ---
        if mesh.is_watertight and mesh.volume < 0:
            b = snap(); t0 = time.perf_counter()
            trimesh.repair.fix_inversion(mesh)
            add("вывернутость", "тело было вывернуто наизнанку, исправлено", t0, {}, b)

    need_remesh = level == "remesh" or (level == "auto" and not mesh.is_watertight)
    if need_remesh:
        b = snap(); t0 = time.perf_counter()
        mesh = voxel_remesh(mesh, pitch=pitch, close_k=close_k)
        add("перестройка", "тело перестроено по вокселям — форма упрощена ради "
                           "гарантированной замкнутости",
            t0, {"pitch": pitch, "close_k": close_k}, b)

    after = diagnose(mesh)
    solid = Solid.wrap(from_trimesh(mesh), name=name or "импорт", op="repair")
    return RepairResult(solid=solid, mesh=mesh, before=before, after=after,
                        steps=tuple(steps))
