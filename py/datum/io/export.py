"""Выгрузка деталей для слайсера: STL и 3MF в позе печати.

До этого модуля Datum проверял деталь перед печатью — и не отдавал файла, по
которому печатать. На `export_mesh` ссылались и сообщения перил, и заметки, а
операции не было; `lib3mf` стоял в зависимостях и не использовался нигде.

ТРИ ПРАВИЛА.

ПОЗА — ТА ЖЕ, ЧТО ПРОВЕРЕНА. Деталь ставится на стол функцией
`geom.xform.upright` по `print_up` детали — той же, которой пользуются
проверки печатности. Две функции разошлись бы, и проверено было бы одно, а
напечатано другое. Не объявлена поза — деталь уходит как построена, и отчёт
говорит это прямо.

ЗАПИСАННОЕ ПЕРЕЧИТЫВАЕТСЯ. Файл читается обратно, и объём сверяется с
каноническим: замкнутость, число треугольников, объём. Трещина, появившаяся
на границе с мешем, — ровно тот класс отказов, ради которого у Datum страж;
не проверив записанное, мы бы доверились писателю.

ФАЙЛ ПИШЕТСЯ АТОМАРНО: сначала рядом, потом переименование. Слайсер, открытый
на прежней выгрузке, не увидит половины файла.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import locale
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from datum.geom.convert import vertices_faces
from datum.geom.solid import Solid
from datum.geom.xform import move, upright

__all__ = ["ExportError", "Exported", "on_bed", "bed_pose", "write_stl", "write_3mf",
           "safe_name"]

#: Насколько объём перечитанного файла может отличаться от канонического.
#: STL хранит координаты во float32 — это около 1e-7 относительно на
#: координату и порядка 1e-6 на объём; больше — уже не округление, а порча.
VOLUME_REL_TOL = 1e-5


class ExportError(RuntimeError):
    """Файл не записался или записался не тем, чем должен был."""


@dataclass(frozen=True, slots=True)
class Exported:
    path: Path
    triangles: int
    volume: float
    note: str = ""          # что пришлось поправить при записи (см. io.precision)


def safe_name(name: str) -> str:
    """Имя детали → имя файла. Кириллица остаётся: слайсеры её показывают.

    Убирается только то, что ломает путь: разделители, управляющие символы и
    пробелы по краям. Пустое имя — отказ, а не «unnamed.stl».
    """
    cleaned = re.sub(r'[\x00-\x1f/\\:*?"<>|]', "_", str(name)).strip().strip(".")
    if not cleaned:
        raise ExportError(f"из имени детали {name!r} не выходит имени файла")
    return cleaned


def on_bed(solid: Solid, up: str, bed: tuple[float, float, float]) -> Solid:
    """Поставить деталь на стол: ось `up` вверх, низ на Z=0, центр — по центру стола.

    Пустой `up` — позы нет, деталь остаётся как построена (но на столе).
    """
    from datum.geom.measure import bbox

    placed = upright(solid, up) if up else solid
    lo, hi = bbox(placed)
    cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
    return move(placed, dx=bed[0] / 2 - cx, dy=bed[1] / 2 - cy, dz=-lo[2])


def bed_pose(solid: Solid, up: str, bed: tuple[float, float, float]):
    """Поза детали на столе функцией — чтобы так же поставить её зоны.

    `on_bed` центрирует по габариту самой детали; зона, поставленная им же,
    центрировалась бы по своему и уехала бы с места.
    """
    from datum.geom.measure import bbox

    placed = upright(solid, up) if up else solid
    lo, hi = bbox(placed)
    dx, dy, dz = bed[0] / 2 - (lo[0] + hi[0]) / 2, bed[1] / 2 - (lo[1] + hi[1]) / 2, -lo[2]

    def pose(other: Solid) -> Solid:
        turned = upright(other, up) if up else other
        return move(turned, dx=dx, dy=dy, dz=dz)

    return pose


def _atomic_bytes(path: Path, write) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=path.suffix, dir=path.parent)
    os.close(fd)
    try:
        write(Path(tmp))
        # mkstemp создаёт файл 0600 — для выгрузки это неверно: её читает
        # слайсер, возможно, от другого пользователя. Права — как у обычного
        # файла, по umask.
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(tmp, 0o666 & ~umask)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _verify(path: Path, expected_volume: float, expected_tris: int) -> None:
    """Перечитать файл и сверить с каноном. Не сошлось — файл удаляется."""
    try:
        if path.suffix.lower() == ".3mf":
            # 3MF перечитывается СВОИМ читателем (lib3mf), а не trimesh: тот
            # строит дерево lxml и на 1.5 млн треугольников берёт 2.4 ГБ против
            # 0.18 (замер в шапке `read3mf.py`) — каждая выгрузка и нарезка
            # крупной детали платила этим пиком в воркере с пределом памяти.
            from datum.io.read3mf import read_3mf

            verts, faces = read_3mf(path)
            # Замкнутость судится так, как файл прочтёт слайсер: вершины,
            # совпавшие во float32, для него ОДНА вершина. Без слияния листы,
            # касающиеся ребром, выглядели бы замкнутыми — а слайсер увидел
            # бы ребро с четырьмя гранями (ради этого и есть `io/precision`).
            _, merged = np.unique(np.asarray(verts, np.float32), axis=0, return_inverse=True)
            n_faces = len(faces)
            closed = _closed(np.asarray(merged).reshape(-1)[np.asarray(faces, np.int64)])
            volume = _signed_volume(np.asarray(verts, float), np.asarray(faces, np.int64))
        else:
            import trimesh

            mesh = trimesh.load(path, force="mesh")
            n_faces, closed, volume = len(mesh.faces), bool(mesh.is_watertight), float(mesh.volume)
        problems = []
        if n_faces != expected_tris:
            problems.append(f"треугольников {n_faces} вместо {expected_tris}")
        if not closed:
            problems.append("меш не замкнут")
        scale = max(abs(expected_volume), 1e-9)
        if abs(volume - expected_volume) / scale > VOLUME_REL_TOL:
            problems.append(f"объём {volume:.4f} мм³ вместо {expected_volume:.4f}")
    except Exception as e:                        # pragma: no cover — нечитаемый файл
        problems = [f"не читается: {type(e).__name__}: {e}"]
    if problems:
        path.unlink(missing_ok=True)
        raise ExportError(f"{path.name}: записанное не совпало с деталью — "
                          + "; ".join(problems) + ". Файл удалён")


def _mesh(solid: Solid) -> tuple[np.ndarray, np.ndarray, float, str]:
    """Сетка для записи: вершины, слипшиеся бы во float32, уже разведены.

    Сверка после записи — с ЭТОЙ сеткой: файл обязан быть ровно тем, что
    решено записать. Отличие же её от детали ограничено сдвигом (микрон) и
    названо в отчёте вместе с изменением объёма. Сверять с объёмом ядра
    нельзя: угол большой грани, сдвинутый на микрон, меняет объём куба
    в 20 мм на 3·10⁻⁵ — больше допуска, рассчитанного на одно округление.
    """
    from datum.io.precision import separate_coincident

    verts, faces = vertices_faces(solid.manifold)
    if faces.size == 0:
        raise ExportError(f"{solid.name or 'деталь'}: пустое тело — выгружать нечего")
    volume = float(solid.manifold.volume())
    sep = separate_coincident(verts, faces)
    if not sep.moved:
        return verts, faces, volume, sep.note()
    written = _signed_volume(sep.verts, faces)
    note = sep.note() + f"; объём от этого изменился на {written - volume:+.4f} мм³"
    return sep.verts, faces, written, note


def _closed(faces: np.ndarray) -> bool:
    """Сетка замкнута и ориентирована: каждое направленное ребро — ровно один
    раз, и у каждого есть встречное (то, что trimesh зовёт watertight)."""
    faces = np.asarray(faces, np.int64)
    if not len(faces):
        return False
    edges = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    span = int(edges.max()) + 1
    forward = edges[:, 0] * span + edges[:, 1]
    backward = edges[:, 1] * span + edges[:, 0]
    if len(np.unique(forward)) != len(forward):
        return False
    return bool(np.array_equal(np.sort(forward), np.sort(backward)))


def _signed_volume(verts: np.ndarray, faces: np.ndarray) -> float:
    a, b, c = (verts[faces[:, k]] for k in range(3))
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def write_stl(path: Path, solid: Solid) -> Exported:
    """Двоичный STL. Миллиметры, Z вверх — единиц STL не хранит, слайсеры ждут мм."""
    import trimesh

    verts, faces, volume, note = _mesh(solid)
    mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
    _atomic_bytes(path, lambda tmp: tmp.write_bytes(mesh.export(file_type="stl")))
    _verify(path, volume, len(faces))
    return Exported(path, len(faces), volume, note)


@contextlib.contextmanager
def _keep_locale():
    """Вернуть локаль процесса такой, какой она была до lib3mf.

    `WriteToFile` у lib3mf 2.5 переводит весь процесс в локаль «C» и обратно не
    возвращает. После этого кодировка по умолчанию — ASCII, и любой
    `write_text("…")` с кириллицей без явной кодировки падает — где угодно в
    процессе, хоть в скрипте детали. Нашлось это тестом перил: запись «ок» в
    проект после выгрузки давала пустой файл.
    """
    saved = locale.setlocale(locale.LC_ALL)
    try:
        yield
    finally:
        locale.setlocale(locale.LC_ALL, saved)


def _set_geometry(lib3mf, obj, verts: np.ndarray, faces: np.ndarray) -> None:
    """Передать меш в lib3mf одним куском.

    Публичный `SetGeometry` принимает списки объектов Position/Triangle и
    копирует их поштучно: 260 тыс. треугольников — 2.3 с, самая тяжёлая сборка
    корпуса вышла бы около 12. А структуры эти упакованы плотно (`float32×3`,
    `uint32×3`, `_pack_ = 1`) — ровно раскладка массивов numpy, и та же
    C-функция берёт их без копирования. Её имя и порядок аргументов — из
    самой привязки (`Lib3MF.py`, `SetGeometry`); не нашлось их — идём
    публичным путём.
    """
    import ctypes

    v = np.ascontiguousarray(verts, dtype=np.float32)
    f = np.ascontiguousarray(faces, dtype=np.uint32)
    call = getattr(getattr(getattr(obj, "_wrapper", None), "lib", None),
                   "lib3mf_meshobject_setgeometry", None)
    if call is None or ctypes.sizeof(lib3mf.Position) != 12 \
            or ctypes.sizeof(lib3mf.Triangle) != 12:   # pragma: no cover
        positions = []
        for x, y, z in v.tolist():
            p = lib3mf.Position()
            p.Coordinates[0], p.Coordinates[1], p.Coordinates[2] = x, y, z
            positions.append(p)
        triangles = []
        for a, b, c in f.tolist():
            t = lib3mf.Triangle()
            t.Indices[0], t.Indices[1], t.Indices[2] = a, b, c
            triangles.append(t)
        obj.SetGeometry(positions, triangles)
        return
    pv = (lib3mf.Position * len(v)).from_buffer(v)
    pf = (lib3mf.Triangle * len(f)).from_buffer(f)
    obj._wrapper.checkError(obj, call(obj._handle, ctypes.c_uint64(len(v)), pv,
                                      ctypes.c_uint64(len(f)), pf))


def write_3mf(path: Path, solid: Solid, *, title: str, description: str = "") -> Exported:
    """3MF через lib3mf: единица — миллиметр, объект назван по детали.

    Описание (вердикт проверок) уходит в метаданные `Description`: слайсер его
    не покажет, но в файле оно останется вместе с тем, что напечатано.
    """
    import lib3mf

    verts, faces, volume, note = _mesh(solid)
    wrapper = lib3mf.get_wrapper()
    model = wrapper.CreateModel()
    model.SetUnit(lib3mf.ModelUnit.MilliMeter)
    obj = model.AddMeshObject()
    obj.SetName(title)
    _set_geometry(lib3mf, obj, verts, faces)
    if not obj.IsManifoldAndOriented():
        raise ExportError(f"{title}: lib3mf считает меш незамкнутым или вывернутым")
    model.AddBuildItem(obj, wrapper.GetIdentityTransform())
    meta = model.GetMetaDataGroup()
    stamp = _dt.date.today().isoformat()
    for key, value in (("Title", title), ("Application", "Datum"),
                       ("CreationDate", stamp), ("Description", description)):
        if value:
            meta.AddMetaData("", key, value, "string", True)
    def write(tmp: Path) -> None:
        with _keep_locale():
            model.QueryWriter("3mf").WriteToFile(str(tmp))

    _atomic_bytes(path, write)
    _verify(path, volume, len(faces))
    return Exported(path, len(faces), volume, note)
