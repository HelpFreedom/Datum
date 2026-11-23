"""Картинки прогона для агента: лента поз и графики моментов.

Рисует растеризатор Datum (`viz.render`), а не рендер MuJoCo: тому нужен
OpenGL (PyOpenGL, EGL/osmesa), а позы — это те же детали Datum в других
местах. Цвета звеньев — по порядку, подписи — в легенде.
"""

from __future__ import annotations

from io import BytesIO

import numpy as np

from datum.sim.compile import Compiled

__all__ = ["filmstrip", "torque_sheet", "stress_sheet", "stress_field_colours", "LINK_COLOURS"]

LINK_COLOURS = ("#9aa4b2", "#c9772e", "#2e6f8e", "#8e3a2e", "#6b9a4a", "#b08ad0",
                "#d9c9a0", "#5f8fbf", "#c25b7a", "#7a7f88")


def _hex(c: str) -> list[int]:
    return [int(c[i:i + 2], 16) for i in (1, 3, 5)]


def _posed_mesh(comp: Compiled, mats: dict[str, np.ndarray]):
    from datum.geom.booleans import compose
    from datum.geom.convert import vertices_faces
    from datum.geom.xform import place

    verts, faces, colours = [], [], []
    offset = 0
    for k, (name, lk) in enumerate(comp.links.items()):
        solid = place(compose(lk.solids), mats[name])
        v, f = vertices_faces(solid.manifold)
        if len(f) == 0:
            continue
        verts.append(np.asarray(v, float))
        faces.append(np.asarray(f, np.int64) + offset)
        colours.append(np.tile(_hex(LINK_COLOURS[k % len(LINK_COLOURS)]), (len(f), 1)))
        offset += len(v)
    return np.vstack(verts), np.vstack(faces), np.vstack(colours).astype(float)


def filmstrip(comp: Compiled, poses: list[tuple[str, dict[str, np.ndarray]]], *,
              title: str, tile: int = 360) -> bytes:
    """Лента: изометрия механизма в каждой позе — ОДИН масштаб и одна рамка на
    все кадры (иначе позы не сравнить глазами), легенда звеньев — в шапке."""
    from PIL import Image, ImageDraw

    from datum.viz.render import render_views
    from datum.viz.section import TEXT, _font

    meshes = [_posed_mesh(comp, mats) for _, mats in poses]
    allv = np.vstack([m[0] for m in meshes])
    frame = (tuple(allv.min(0)), tuple(allv.max(0)))
    sheets = []
    for (label, _), (v, f, c) in zip(poses, meshes):
        png = render_views(v, f, title=label, face_colors=c, views=("изометрия",), tile=tile,
                           frame=frame)
        sheets.append(Image.open(BytesIO(png)).convert("RGB"))
    cols = min(len(sheets), 3)
    rows = (len(sheets) + cols - 1) // cols
    w = max(s.width for s in sheets)
    h = max(s.height for s in sheets)
    head = 44
    sheet = Image.new("RGB", (cols * w, rows * h + head), (24, 26, 31))
    draw = ImageDraw.Draw(sheet)
    draw.text((12, 12), title, font=_font(18), fill=TEXT)
    small = _font(13)
    x = cols * w - 12
    for k, name in reversed(list(enumerate(comp.links))):
        x -= draw.textlength(name, font=small)
        draw.text((x, 16), name, font=small, fill=TEXT)
        x -= 16
        draw.rectangle([x, 17, x + 11, 28], fill=tuple(_hex(LINK_COLOURS[k % len(LINK_COLOURS)])))
        x -= 14
    for i, s in enumerate(sheets):
        sheet.paste(s, ((i % cols) * w, head + (i // cols) * h))
    out = BytesIO()
    sheet.save(out, "PNG", optimize=True)
    return out.getvalue()


def torque_sheet(comp: Compiled, result, *, title: str) -> bytes:
    """По суставу на график: нужный момент по плану, отданный в прямом прогоне
    и предел привода (пунктир). Видно, ГДЕ по времени не хватает."""
    from PIL import Image, ImageDraw

    from datum.viz.chart import LINES, Mark, Series, render_chart
    from datum.viz.section import TEXT, _font

    charts = []
    for k, j in enumerate(result.plan.drivers):
        joint = comp.mechanism.links[j].joint
        series = [Series(result.plan.t.tolist(), result.required[:, k].tolist(),
                         label="нужно по плану", colour=LINES[0])]
        if result.applied is not None:
            series.append(Series(result.plan.t.tolist(), result.applied[:, k].tolist(),
                                 label="отдал привод", colour=LINES[1], width=1))
        marks = []
        if joint.drive is not None:
            lim = joint.drive.limit
            marks = [Mark(label=f"предел {lim:g}", y=lim), Mark(label="", y=-lim)]
        png = render_chart(series, title=f"{j}: момент, {joint.effort_unit}", x_label="время, с",
                           marks=marks, size=(920, 300))
        charts.append(Image.open(BytesIO(png)).convert("RGB"))
    w = max(c.width for c in charts)
    sheet = Image.new("RGB", (w, sum(c.height for c in charts) + 40), (24, 26, 31))
    ImageDraw.Draw(sheet).text((12, 10), title, font=_font(18), fill=TEXT)
    y = 40
    for c in charts:
        sheet.paste(c, (0, y))
        y += c.height
    out = BytesIO()
    sheet.save(out, "PNG", optimize=True)
    return out.getvalue()


def stress_field_colours(node_vm):
    """Цвета вершин поля: от нуля до САМОГО нагруженного места звена. Шкала до предела
    материала прятала распределение (звено на 3 % предела было сплошь зелёным), а
    вопрос карты — ГДЕ слабое место; доля предела — числом в подписи и на кнопке."""
    import numpy as np

    from datum.sim.stress import heat_rgb

    top = float(np.max(node_vm)) if len(node_vm) else 0.0
    return heat_rgb(np.asarray(node_vm, float) / max(top, 1e-12))


def stress_sheet(report, *, title: str) -> bytes:
    """Поле напряжений звена в худший момент: четыре вида, цвет — от свободного
    (зелёный) до самого нагруженного места (красный), отметка — пик вне опоры."""
    from datum.viz.render import render_views

    colours = stress_field_colours(report.node_vm)[report.faces].mean(axis=1)
    if report.strength_z:
        scale = (f"оценка {report.value_mpa:.2f} МПа ({report.basis}) = "
                 f"{report.utilization * 100:.0f} % предела между слоями {report.strength_z:g} МПа")
    else:
        scale = f"оценка {report.value_mpa:.2f} МПа (предела материала нет)"
    note = (f"t = {report.t:.2f} с. Цвет — от свободного (зелёный) до самого нагруженного "
            f"места звена (красный); {scale}; 99 % объёма — до {report.p99_mpa:.2f} МПа; прогиб "
            f"до {report.max_displacement_mm:.3f} мм")
    return render_views(report.verts, report.faces, title=title, note=note,
                        face_colors=colours, marks=((tuple(report.peak_at), "пик"),))
