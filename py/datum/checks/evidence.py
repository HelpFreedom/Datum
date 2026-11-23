"""Доказательства к проваленным проверкам.

План обещал: каждый провал сам приносит картинку, на которой виновное место
видно глазом. До сих пор поле `evidence` у результата существовало, а URL в нём
был пуст — и разбирать «пересечение 12.4 мм³» приходилось руками.

Картинка строится ТОЛЬКО при провале. На успешной проверке она никому не нужна,
а стоит столько же.

Плоскость сечения выбирается сама, и правило здесь не очевидное. Первым
напрашивается «резать перпендикулярно самой короткой оси виновной области» —
тогда сама область видна крупнее всего. Замер на настоящем провале показал, что
это плохо: срез проходил через стенку, крышка в нём целиком совпадала с
пересечением, и понять, КУДА она уехала, было нельзя.

Поэтому плоскость проходит через ЦЕНТР виновной области (иначе беда в углу в
срез не попадёт вовсе), а из трёх возможных берётся та, где в сечение попадает
БОЛЬШЕ МАТЕРИАЛА обеих деталей. Больше материала — больше контекста: видно не
только наложение, но и куда деталь съехала относительно своего места.
"""

from __future__ import annotations

from datum.checks.result import Artifact
from datum.geom.solid import Solid

__all__ = ["best_plane", "section_of", "intersection_evidence", "pair_evidence",
           "escape_evidence"]

AXIS_TO_PLANE = {0: "yz", 1: "xz", 2: "xy"}

#: Цвета совпадают с окном: деталь серо-синяя, вторая оранжевая, беда красная.
COLOUR_A = ((154, 164, 178, 90), (154, 164, 178, 255))
COLOUR_B = ((201, 114, 59, 105), (201, 114, 59, 255))
COLOUR_BAD = ((229, 140, 140, 190), (229, 140, 140, 255))


def section_of(solid: Solid, plane: str, at: float):
    from datum.geom.measure import section_xy, section_xz, section_yz

    return {"xy": section_xy, "xz": section_xz, "yz": section_yz}[plane](solid, at)


#: Какие плоскости содержат данную ось. Срез, в котором движение лежит В
#: ПЛОСКОСТИ, показывает сдвиг; срез поперёк движения показал бы две почти
#: одинаковые картинки и ничего не объяснил.
PLANES_WITH_AXIS = {0: ("xy", "xz"), 1: ("xy", "yz"), 2: ("xz", "yz")}


def best_plane(*parts: Solid, through: Solid | None = None,
               allowed: tuple[str, ...] | None = None) -> tuple[str, float]:
    """Где резать, чтобы провал был понятен.

    `through` — область, через которую срез обязан пройти (само пересечение).
    Из трёх плоскостей, проходящих через её центр, берётся та, в которой видно
    больше материала: контекст важнее крупности самой беды.
    """
    from datum.geom.booleans import union
    from datum.geom.measure import com

    target = through if through is not None else union(list(parts))
    centre = com(target)

    best, best_area = None, -1.0
    for axis, plane in AXIS_TO_PLANE.items():
        if allowed is not None and plane not in allowed:
            continue
        at = float(centre[axis])
        area = 0.0
        for part in parts:
            try:
                area += float(section_of(part, plane, at).area)
            except Exception:                  # pragma: no cover
                continue
        if area > best_area:
            best, best_area = (plane, at), area
    return best if best is not None else ("xy", float(centre[2]))


def _render(layers, plane, at, title, note) -> Artifact | None:
    from datum.viz.section import Layer, render_section

    drawn = [Layer(geom=geom, label=label, fill=fill, line=line)
             for geom, label, (fill, line) in layers
             if geom is not None and not geom.is_empty]
    if not drawn:
        return None
    png = render_section(drawn, plane=plane, at=at, title=title, note=note)
    return Artifact(kind="png", caption=note or title, data=png)


def intersection_evidence(a: Solid, b: Solid, *, a_name: str, b_name: str,
                          title: str, note: str) -> Artifact | None:
    """Где именно тела влезли друг в друга.

    Сечение проводится через саму область пересечения, а не через середину
    детали: беда может сидеть в углу, и срез «по центру» её не покажет вовсе.
    """
    from datum.geom.booleans import intersect
    from datum.geom.measure import vol

    overlap = intersect([a, b])
    if vol(overlap) <= 0:
        return None

    plane, at = best_plane(a, b, through=overlap)
    return _render(
        [(section_of(a, plane, at), a_name, COLOUR_A),
         (section_of(b, plane, at), b_name, COLOUR_B),
         (section_of(overlap, plane, at), "пересечение", COLOUR_BAD)],
        plane, at, title, note)


def pair_evidence(a: Solid, b: Solid, *, a_name: str, b_name: str,
                  title: str, note: str, plane: str | None = None,
                  at: float | None = None) -> Artifact | None:
    """Два тела в одном сечении — когда пересечения нет, а показать надо.

    Так выглядит провал зазора: детали не налезают, но стоят слишком близко, и
    на картинке видно, где именно.
    """
    if plane is None or at is None:
        plane, at = best_plane(a, b)
    return _render(
        [(section_of(a, plane, at), a_name, COLOUR_A),
         (section_of(b, plane, at), b_name, COLOUR_B)],
        plane, at, title, note)


def escape_evidence(part: Solid, holder: Solid, direction, shift: float, *,
                    part_name: str, holder_name: str,
                    title: str, note: str) -> Artifact | None:
    """Провал зацепа: деталь в том положении, где проверка её и поймала.

    Плоскость обязана СОДЕРЖАТЬ направление движения — иначе сдвиг на картинке
    не виден вовсе, и понять, ушла деталь или осталась, нельзя.
    """
    from datum.geom.booleans import intersect
    from datum.geom.measure import vol
    from datum.geom.xform import move

    axis = max(range(3), key=lambda i: abs(direction[i]))
    moved = move(part, dx=direction[0] * shift, dy=direction[1] * shift,
                 dz=direction[2] * shift)

    overlap = intersect([moved, holder])
    has_overlap = vol(overlap) > 1e-9
    plane, at = best_plane(moved, holder,
                           through=overlap if has_overlap else moved,
                           allowed=PLANES_WITH_AXIS[axis])

    layers = [(section_of(holder, plane, at), holder_name, COLOUR_A),
              (section_of(moved, plane, at), f"{part_name} (сдвиг {shift:.2f} мм)",
               COLOUR_B)]
    if has_overlap:
        layers.append((section_of(overlap, plane, at), "зацеп", COLOUR_BAD))
    return _render(layers, plane, at, title, note)
