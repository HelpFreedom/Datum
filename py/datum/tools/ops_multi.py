"""Несколько видов и срезов одним вызовом.

Откуда (аудит 2026-11-21, §3.3 и §5.4). В Gear Cube `section` вызван 205 раз, а
`part_view` — 203: агент смотрит деталь за деталью и высоту за высотой, и
каждый взгляд — отдельный ход, а ход при длинном контексте — перечитывание
всего контекста. Окно делало то же с крышками среза: по заданию на каждую
видимую деталь на каждую остановку ползунка.

Здесь нет своей геометрии: операции зовут `part_view` и `section` по очереди в
одном процессе (проект открыт один раз) — числа и картинки те же до байта.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from datum.tools.ops import Plane, Pose, Project, _err, _guard, open_project, part_view, section
from datum.tools.registry import Arg, op

__all__ = ["part_views", "sections", "section_scene", "MAX_AT_ONCE"]

#: Сколько картинок несёт один ответ агенту (`mcp_adapter`): больше деталей или
#: высот за вызов — отказ, а не молча потерянные картинки.
MAX_AT_ONCE = 4


@op("part_views", cli_name="views", group="замеры", isolate=True,
    title="Виды нескольких деталей одним вызовом",
    summary=("Нарисовать несколько деталей — каждую с четырёх сторон, как part_view, — "
             f"одним вызовом (до {MAX_AT_ONCE}): все картинки приходят в одном ответе. "
             "pose и color_by — как у part_view. Крупный план и поворот — у part_view."))
async def part_views(
    project: Project,
    parts: Annotated[list[str], Arg(help=f"имена деталей, до {MAX_AT_ONCE}", metavar="ДЕТАЛЬ")],
    pose: Annotated[Pose, Arg(help="built — как построена, print — как на столе")] = "built",
    color_by: Annotated[Literal["part", "print"],
                        Arg(help="part — цветом детали, print — карта печатности")] = "part",
    style: Annotated[Literal["technical", "render"],
                     Arg(help="technical — грани и рёбра, render — свет и тень")] = "technical",
) -> dict[str, Any]:
    names = list(dict.fromkeys(parts or []))
    if not names:
        return _err("не названо ни одной детали")
    if len(names) > MAX_AT_ONCE:
        return _err(f"деталей {len(names)}: за вызов — до {MAX_AT_ONCE} (столько картинок "
                    f"несёт один ответ); остальные — следующим вызовом")
    views: dict[str, Any] = {}
    lines: list[str] = []
    failed: dict[str, str] = {}
    for name in names:
        one = await part_view(project, name, pose, color_by, style)
        if one.get("error"):
            failed[name] = str(one["error"])
            lines.append(f"{name}: НЕ НАРИСОВАНА — {one['error']}")
            continue
        lines.append(str(one.pop("report", "")))
        views[name] = one
    if not views:
        return _err("\n".join(lines))
    return {"views": views, "failed": failed, "report": "\n".join(lines)}


@op("sections", cli_name="sections", group="замеры", isolate=True,
    title="Несколько сечений детали одним вызовом",
    summary=("Сечения одной детали одной плоскостью на нескольких координатах "
             f"(до {MAX_AT_ONCE}) одним вызовом: площадь, контуры, дыры и картинка "
             "каждого — в одном ответе. Так смотрят, как меняется сечение по высоте "
             "(стенка канала, шейка, гнездо), не тратя ход на каждую высоту."))
async def sections(
    project: Project,
    part: Annotated[str, Arg(help="имя детали")],
    plane: Annotated[Plane, Arg(help="плоскость: xy, xz или yz")],
    at: Annotated[list[float], Arg(help=f"координаты плоскости, мм, до {MAX_AT_ONCE}",
                                   metavar="ЧИСЛО")],
    image: Annotated[bool, Arg(help="рисовать каждое сечение в PNG")] = True,
) -> dict[str, Any]:
    levels = list(at or [])
    if not levels:
        return _err("не названо ни одной координаты")
    if len(levels) > MAX_AT_ONCE:
        return _err(f"координат {len(levels)}: за вызов — до {MAX_AT_ONCE} (столько картинок "
                    f"несёт один ответ)")
    out: list[dict] = []
    lines: list[str] = []
    for level in levels:
        one = await section(project, part, plane, float(level), False, image)
        if one.get("error"):
            return _err(str(one["error"]))          # деталь не собралась — не собралась для всех
        lines.append(str(one.pop("report", "")))
        out.append(one)
    return {"part": part, "plane": plane, "sections": out, "report": "\n".join(lines)}


@op("section_scene", cli_name="section-scene", group="замеры", isolate=True,
    title="Срез всех деталей одной плоскостью",
    summary=("Контуры сечения нескольких деталей одной плоскостью — одним заданием. "
             "Так окно рисует крышки среза: прежде на каждую остановку ползунка шло по "
             "заданию на каждую видимую деталь. parts пусто — все детали."))
async def section_scene(
    project: Project,
    plane: Annotated[Plane, Arg(help="плоскость: xy, xz или yz")],
    at: Annotated[float, Arg(help="координата плоскости, мм")],
    parts: Annotated[list[str] | None, Arg(help="имена деталей; пусто — все",
                                           metavar="ДЕТАЛЬ")] = None,
) -> dict[str, Any]:
    def names():
        pr = open_project(project)
        return list(dict.fromkeys(parts)) if parts else list(pr.parts)

    listed = _guard(names)
    if isinstance(listed, dict):
        return listed
    done: dict[str, Any] = {}
    failed: dict[str, str] = {}
    for name in listed:
        one = await section(project, name, plane, at, True, False)
        if one.get("error"):
            # Несобравшаяся деталь не должна гасить срез остальных.
            failed[name] = str(one["error"])
            continue
        one.pop("report", None)
        done[name] = one
    lines = [f"срез {plane}={at:g}: деталей {len(done)}, пустых "
             f"{sum(1 for d in done.values() if d.get('empty'))}"]
    lines += [f"  {n}: {e}" for n, e in failed.items()]
    return {"plane": plane, "at": at, "parts": done, "failed": failed,
            "report": "\n".join(lines)}
