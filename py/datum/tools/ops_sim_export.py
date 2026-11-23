"""Механизм наружу: GLB с движением, MJCF и URDF.

Откуда (аудит 2026-11-21, §7.6). Дважды владелец просил видео механизма, и
агент каждый раз писал свой рендер и ffmpeg; кинематику руки-v3 он руками
переносил в прошивку. Модель MuJoCo и запись движения в Datum уже есть — их
осталось отдать в чужих форматах:

* **GLB** — звенья узлами и анимация сценария: его берут Kadr (`@kadr/three`),
  Blender, three.js. Стандарт glTF: Y вверх, метры;
* **MJCF** — та же модель MuJoCo, что считает симуляция (массы, инерции,
  суставы, приводы), плюс сетки звеньев для вида и связи ведомых суставов;
* **URDF** — то же для ROS, PyBullet, Pinocchio, прошивки.

Записанное перечитывается и сверяется с моделью, по которой шёл расчёт, — как
у `export`: файл, не совпавший с проектом, хуже отсутствующего.
"""

from __future__ import annotations

from typing import Annotated, Any

from datum.tools.ops import Project, _err, _guard, open_project
from datum.tools.registry import Arg, op

__all__ = ["sim_export", "FORMATS"]

FORMATS = ("glb", "mjcf", "urdf")


@op("sim_export", cli_name="sim-export", group="симуляция", isolate=True, danger="mutates",
    memory_headroom_mb=2048,
    title="Выгрузить механизм: GLB с движением, MJCF, URDF",
    summary=("Отдать механизм чужим программам, в output/sim/<механизм>/: GLB (звенья "
             "узлами и анимация сценария — для Kadr, Blender, three.js; Y вверх, метры), "
             "MJCF (та же модель MuJoCo, что считает симуляция, с сетками звеньев) и URDF "
             "(ROS, PyBullet, прошивка). Записанное перечитывается и сверяется с моделью "
             "расчёта. Без сценария GLB — неподвижный, а грузов в моделях нет. Видео и "
             "кадры своим рендером не делайте: анимированный GLB берёт Kadr."))
async def sim_export(
    project: Project,
    mechanism: Annotated[str, Arg(help="имя механизма (@mechanism)")],
    scenario: Annotated[str, Arg(help="сценарий: его движение пишется в GLB, его грузы — "
                                      "в MJCF и URDF; пусто — без движения и грузов")] = "",
    formats: Annotated[list[str] | None,
                       Arg(help="что писать: glb, mjcf, urdf; пусто — всё",
                           metavar="ФОРМАТ")] = None,
) -> dict[str, Any]:
    def run():
        import numpy as np

        from datum.geom.booleans import compose
        from datum.geom.convert import vertices_faces
        from datum.io.export import _atomic_bytes, safe_name, write_stl
        from datum.io.glb import links_to_glb
        from datum.sim.mechanism import MechanismError
        from datum.sim.model import check_urdf, write_mjcf
        from datum.sim.pictures import LINK_COLOURS
        from datum.sim.urdf import urdf_text

        wanted = [f.strip().lower() for f in (formats or FORMATS)]
        unknown = [f for f in wanted if f not in FORMATS]
        if unknown or not wanted:
            return _err(f"формат {unknown or wanted}: есть {', '.join(FORMATS)}")
        pr = open_project(project)
        comp = pr.compiled_mechanism(mechanism)
        mech = comp.mechanism
        if scenario and scenario not in mech.scenarios:
            return _err(f"у механизма {mechanism!r} нет сценария {scenario!r}; есть: "
                        f"{sorted(mech.scenarios) or 'никаких'}")
        payloads = tuple(mech.scenarios[scenario].payloads) if scenario else ()
        out_dir = pr.root / "output" / "sim" / safe_name(mechanism)
        colours = {n: LINK_COLOURS[k % len(LINK_COLOURS)] for k, n in enumerate(comp.links)}
        bodies = {n: compose(lk.solids) for n, lk in comp.links.items()}
        lines = [f"механизм {mechanism}: звеньев {len(comp.links)}, масса "
                 f"{comp.total_mass_kg * 1000:.0f} г"
                 + (f", сценарий «{scenario}»" if scenario else ", без сценария")]
        files: dict[str, Any] = {}

        def rel(path) -> str:
            return str(path.relative_to(pr.root))

        if "glb" in wanted:
            times = poses = None
            if scenario:
                _, r = pr.simulate(mechanism, scenario, geometry=False)
                order = [r.frames["links"].index(n) for n in comp.links]
                times = r.frames["t"]
                poses = np.asarray(r.frames["poses"], float)[:, order, :]
            links = [(n, *vertices_faces(bodies[n].manifold), colours[n]) for n in comp.links]
            data = links_to_glb(links, times=times, poses=poses, name=mechanism,
                                animation=scenario)
            path = out_dir / (f"{safe_name(mechanism)}-{safe_name(scenario)}.glb" if scenario
                              else f"{safe_name(mechanism)}.glb")
            _atomic_bytes(path, lambda p: p.write_bytes(data))
            files["glb"] = {"file": rel(path), "bytes": len(data),
                            "frames": 0 if times is None else len(times),
                            "duration_s": 0.0 if times is None else float(times[-1])}
            lines.append(f"GLB → {rel(path)}: {len(data) / 1024:.0f} КБ, "
                         + (f"анимация «{scenario}» {times[-1]:.2f} с, кадров {len(times)}"
                            if times is not None else "без движения (сценарий не назван)")
                         + "; Y вверх, метры — как ждут Kadr, Blender и three.js")

        if "mjcf" in wanted or "urdf" in wanted:
            meshes = {}
            for n, body in bodies.items():
                stl = write_stl(out_dir / "meshes" / f"{safe_name(n)}.stl", body)
                meshes[n] = stl.path.name
            try:
                if "mjcf" in wanted:
                    path = out_dir / "model.xml"
                    info = write_mjcf(comp, path, name=mechanism, meshes=meshes,
                                      colours=colours, payloads=payloads)
                    files["mjcf"] = {"file": rel(path), **info}
                    lines.append(
                        f"MJCF → {rel(path)}: тел {info['bodies']}, суставов {info['joints']}, "
                        f"приводов {info['actuators']}"
                        + (f", связей ведомых {info['couplings']}" if info["couplings"] else "")
                        + f"; перечитано MuJoCo — центры масс сходятся до "
                          f"{info['worst_m'] * 1e6:.3f} мкм")
                    lines.append("  контакты в модели ВЫКЛЮЧЕНЫ, сетки — только вид: невыпуклую "
                                 "сетку MuJoCo заменил бы выпуклой оболочкой. Задевания считает "
                                 "sim_run точной геометрией")
                if "urdf" in wanted:
                    path = out_dir / "robot.urdf"
                    text = urdf_text(comp, name=mechanism, colours=colours, payloads=payloads,
                                     meshes={n: f"meshes/{f}" for n, f in meshes.items()})
                    _atomic_bytes(path, lambda p: p.write_bytes(text.encode("utf-8")))
                    worst = check_urdf(comp, path, payloads=payloads)
                    files["urdf"] = {"file": rel(path), "worst_m": worst}
                    lines.append(f"URDF → {rel(path)}: перечитано MuJoCo — центры масс сходятся "
                                 f"до {worst * 1e6:.3f} мкм; рамка звена — оси сцены в точке "
                                 f"на оси его сустава")
                    lines.append("  пути к сеткам относительные (meshes/…): ROS ждёт "
                                 "package://… — поправьте под свой пакет. Ведомые суставы — "
                                 "<mimic>")
            except MechanismError as e:
                return _err(str(e))
            files["meshes"] = [rel(out_dir / "meshes" / f) for f in meshes.values()]
            lines.append(f"сетки звеньев: {rel(out_dir / 'meshes')}/ — STL, миллиметры, в "
                         f"сцене при нуле (в моделях масштаб 0.001)")
            if payloads:
                lines.append(f"грузы сценария в моделях: {', '.join(payloads)}")
            elif mech.payloads:
                lines.append(f"грузов в моделях нет (они — у сценариев: "
                             f"{', '.join(sorted(mech.payloads))})")
        lines.append("массы и инерции — по настройкам печати и объявленному покупному, "
                     "как в sim_list; трение, КПД и люфт приводов в эти форматы не "
                     "переносятся — они в отчёте sim_run")
        return {"mechanism": mechanism, "scenario": scenario, "files": files,
                "report": "\n".join(lines)}

    return _guard(run)
