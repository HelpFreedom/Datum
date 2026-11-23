"""Геометрическое ядро Datum.

Плоский набор имён — скрипты деталей пишут `from datum.geom import *`.
Имена и порядок аргументов совпадают с `lib/mf.py` из рабочего процесса CAD,
чтобы перенос существующих скриптов был механическим.

Канон — меш (manifold3d). B-rep (build123d/OCCT) подключается отдельно и только
там, где нужны настоящие скругления и STEP.
"""

from datum.geom.booleans import (compose, diff, hull, intersect, split_by_plane,
                                 union)
from datum.geom.features import (chamfer_corners, cup, hole_z, knurl, round_corners, slab,
                                 thread)
from datum.geom.convert import from_trimesh, to_trimesh, vertices_faces
from datum.geom.footprint import fit_extents, footprint, min_square_angle, align_long_axis
from datum.geom.guard import Expect, GuardError, GuardMode, checkpoint, locate_failure
from datum.geom.measure import (area, bbox, centre, com, contains, extents,
                                inter_vol, man, min_gap, occupancy,
                                occupancy_ascii, ray_hit, ray_probe, section_xy,
                                section_xz, section_yz, thickness_at, vol)
from datum.geom.implicit import (SDF, blend_intersect, blend_union, box_sdf,
                                 cyl_z_sdf, diff_sdf, intersect_sdf,
                                 invert_sdf, mesh_sdf, offset_sdf,
                                 plane_sdf, shell_sdf, sphere_sdf,
                                 union_sdf)
from datum.geom.prims import (CIRC_N, box, cone_z, cyl_x, cyl_y, cyl_z, empty,
                              extrude, prism_xy, prism_xz, prism_yz, revolve_z,
                              rounded_slab, sphere)
from datum.geom.profile2d import cs, ring_poly, rings, rrect_poly, slot_poly, teardrop_poly
from datum.geom.solid import Journal, OpRecord, Solid
from datum.geom.sweep import insertion_pocket
from datum.geom.text import text_poly, text_z
from datum.geom.topology import TopologyReport, analyze, topology_report
from datum.geom.xform import (align, anchor, array_linear, array_polar, centre_on, frame,
                              mirror, move, place, rot_about, rotate, scale, stack, upright)

__all__ = [
    # тела и журнал
    "Solid", "OpRecord", "Journal",
    # примитивы
    "empty", "box", "extrude", "prism_xy", "prism_xz", "prism_yz",
    "cyl_x", "cyl_y", "cyl_z", "cone_z", "sphere", "revolve_z", "rounded_slab",
    "CIRC_N",
    # плоские профили
    "cs", "rings", "rrect_poly", "slot_poly", "ring_poly", "teardrop_poly",
    # преобразования
    "move", "rotate", "rot_about", "mirror", "scale", "frame", "place",
    "array_linear", "array_polar", "upright",
    # размещение по точкам габарита — без ручной арифметики координат
    "anchor", "align", "stack", "centre_on",
    # элементы для печати: кромки, стакан, отверстия под винт, резьба, накатка
    "round_corners", "chamfer_corners", "slab", "cup", "hole_z", "thread", "knurl",
    # надписи (тиснение и гравировка)
    "text_poly", "text_z",
    # булевы
    "union", "diff", "intersect", "hull", "compose", "split_by_plane",
    # измерения
    "vol", "area", "bbox", "extents", "centre", "com", "inter_vol", "min_gap",
    "section_xy", "section_xz", "section_yz", "ray_hit", "ray_probe",
    "thickness_at", "contains", "occupancy", "occupancy_ascii", "man",
    # контур предмета в плане (ложементы по скану) и карман по вставке
    "footprint", "align_long_axis", "fit_extents", "min_square_angle",
    "insertion_pocket",
    # топология и страж
    "analyze", "topology_report", "TopologyReport",
    "checkpoint", "locate_failure", "Expect", "GuardError", "GuardMode",
    # мосты
    "to_trimesh", "from_trimesh", "vertices_faces",
]
