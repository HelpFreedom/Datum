"""Сборочная инструкция в картинках — из порядка `assembly.yaml`.

Откуда. Трижды владелец просил «HTML-инструкцию по сборке в картинках», и
агент каждый раз писал свой рендер на один раз (аудит 2026-11-21, §7.6).
Порядок сборки и направления вставки в проекте уже записаны — из них выводятся
проверки собираемости; из них же выводится и инструкция.

Шаг — одна деталь порядка. На картинке слева «до»: уже собранное серым, новая
деталь цветом, отведённая ПРОТИВ вставки, и стрелка, куда она идёт; справа
«после»: деталь на месте. Кадр и масштаб — одни на все шаги, иначе шаги не
читаются как последовательность.

Чего здесь нет: крепежа шага (винты, клей) Datum не знает — в порядке сборки
только детали; покупная деталь попадает в инструкцию, когда она объявлена
деталью (`printed=False`) и стоит в порядке.
"""

from __future__ import annotations

import html
from io import BytesIO
from typing import Annotated, Any

from datum.tools.ops import Project, _err, _guard, open_project
from datum.tools.registry import Arg, op

__all__ = ["assembly_guide"]

#: Уже собранное — серым, чтобы новая деталь читалась с первого взгляда.
PLACED_COLOUR = "#6f7785"
#: Новая деталь шага, когда её собственный цвет серый или не задан.
NEW_COLOUR = "#f2a93b"
#: На сколько деталь отведена на картинке «до» — доля её наибольшего размера.
LIFT_SHARE = 0.7
#: …но не меньше этого, мм: мелкую шайбу иначе не видно отдельно от места.
LIFT_MIN_MM = 12.0

_WORDS = {"+Z": "снизу вверх", "-Z": "сверху вниз", "+X": "вдоль +X", "-X": "вдоль −X",
          "+Y": "вдоль +Y", "-Y": "вдоль −Y"}


def _rgb(colour: str) -> list[int]:
    return [int(colour.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]


def _is_grey(colour: str) -> bool:
    r, g, b = _rgb(colour)
    return max(r, g, b) - min(r, g, b) < 40


def _step_text(n: int, name: str, step, printed: bool, joint) -> str:
    """Что сделать на шаге — одной-двумя фразами."""
    bought = "" if printed else " (покупная или готовая деталь)"
    if n == 1:
        text = f"Основание — «{name}»{bought}. Положите его перед собой: в него встаёт остальное."
    elif step is None or not step.insert:
        text = (f"Поставьте «{name}»{bought} на место. Направление вставки в проекте не "
                f"объявлено — смотрите картинку.")
    else:
        key = step.insert[0].upper()
        way = _WORDS.get(key, f"по {key}")
        if step.method == "snap":
            text = (f"Вдавите «{name}»{bought} {way} до щелчка: это защёлка, она заходит "
                    f"с усилием и прогибом.")
        else:
            text = f"Вставьте «{name}»{bought} {way} до упора: деталь входит свободно, без усилия."
        if len(step.insert) > 1:
            text += f" Можно и иначе: {', '.join(k.upper() for k in step.insert[1:])}."
    if joint is not None:
        lo, hi = joint.limits
        text += (f" После установки деталь {'поворачивается' if joint.kind == 'revolute' else 'ходит'}"
                 f" в пределах {lo:g}…{hi:g} {'°' if joint.kind == 'revolute' else 'мм'} — "
                 f"проверьте рукой, что ход свободен.")
    return text


def _page(title: str, steps: list[dict], unlisted: list[str]) -> str:
    """Самодостаточная страница: картинки лежат рядом, сети не нужно."""
    esc = html.escape
    items = "\n".join(
        f'<section><h2>Шаг {s["n"]} из {len(steps)}. {esc(s["part"])}</h2>'
        f'<p>{esc(s["text"])}</p>'
        f'<img src="{esc(s["file"])}" alt="Шаг {s["n"]}: {esc(s["part"])}"></section>'
        for s in steps)
    tail = ""
    if unlisted:
        tail = ("<section><h2>Не вошло в порядок сборки</h2><p>"
                + esc(", ".join(unlisted)) + " — этих деталей нет в <code>order</code> "
                "файла assembly.yaml, и шага у них нет.</p></section>")
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} — сборка</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 16px/1.5 system-ui, sans-serif; max-width: 960px; margin: 0 auto;
         padding: 24px 16px 64px; background: #16181d; color: #e6e8ec; }}
  h1 {{ font-size: 26px; margin: 0 0 4px; }}
  h2 {{ font-size: 19px; margin: 0 0 6px; }}
  p {{ margin: 0 0 12px; }}
  .lead {{ color: #9aa4b2; margin-bottom: 28px; }}
  section {{ border-top: 1px solid #2c313a; padding: 22px 0 6px; break-inside: avoid; }}
  img {{ width: 100%; height: auto; border-radius: 6px; display: block; }}
  code {{ font-family: ui-monospace, monospace; }}
  @media print {{ body {{ background: #fff; color: #000; }} .lead {{ color: #444; }} }}
</style></head><body>
<h1>{esc(title)}: сборка</h1>
<p class="lead">Шагов: {len(steps)}. Серым — то, что уже собрано; цветом — деталь шага.
Слева — куда она идёт, справа — как стоит.</p>
{items}
{tail}
</body></html>
"""


@op("assembly_guide", cli_name="guide", group="сборка", isolate=True, danger="mutates",
    title="Сборочная инструкция в картинках",
    summary=("Сделать инструкцию по сборке из порядка assembly.yaml: на каждый шаг — "
             "картинка «до» (собранное серым, новая деталь цветом, отведена против "
             "вставки, стрелка) и «после», с подписью, что сделать (вставить, вдавить до "
             "щелчка). Пишет output/guide/step-NN.png и index.html — открыть в браузере "
             "или напечатать. Крепежа Datum не знает: в инструкции только детали порядка."))
async def assembly_guide(
    project: Project,
    title: Annotated[str, Arg(help="заголовок страницы; пусто — имя проекта")] = "",
) -> dict[str, Any]:
    def run():
        import numpy as np
        from PIL import Image

        from datum.checks.motion import direction as parse_direction
        from datum.geom.convert import vertices_faces
        from datum.io.export import _atomic_bytes
        from datum.viz.render import render_views

        pr = open_project(project)
        order = [n for n in pr.assembly.order if n in pr.parts]
        if not order:
            return _err("порядок сборки не записан: в assembly.yaml нет order (детали в том "
                        "порядке, в каком их ставят) — инструкцию вывести не из чего")
        built = pr.build(order)
        broken = [r.line() for r in built.values() if not r.ok]
        if broken:
            return _err("не собрались детали:\n" + "\n".join(broken))
        meshes = {n: vertices_faces(built[n].solid.manifold) for n in order}

        # Куда отведена деталь на картинке «до» — против вставки.
        lifts: dict[str, np.ndarray] = {}
        for n in order[1:]:
            step = pr.assembly.steps.get(n)
            if step is None or not step.insert:
                continue
            v = meshes[n][0]
            reach = max(LIFT_SHARE * float(np.max(v.max(0) - v.min(0))), LIFT_MIN_MM)
            lifts[n] = -np.asarray(parse_direction(step.insert[0]), float) * reach
        lo = np.min([meshes[n][0].min(0) for n in order]
                    + [meshes[n][0].min(0) + d for n, d in lifts.items()], axis=0)
        hi = np.max([meshes[n][0].max(0) for n in order]
                    + [meshes[n][0].max(0) + d for n, d in lifts.items()], axis=0)
        frame = (tuple(float(c) for c in lo), tuple(float(c) for c in hi))

        def picture(placed: list[str], new: str, shift, label: str, arrow) -> Image.Image:
            verts, faces, colours, offset = [], [], [], 0
            for n in [*placed, new]:
                v, f = meshes[n]
                own = pr.parts[n].color or ""
                colour = PLACED_COLOUR if n != new else (
                    own if own and not _is_grey(own) else NEW_COLOUR)
                verts.append(v + shift if n == new and shift is not None else v)
                faces.append(np.asarray(f, np.int64) + offset)
                colours.append(np.tile(_rgb(colour), (len(f), 1)))
                offset += len(v)
            png = render_views(np.vstack(verts), np.vstack(faces), title=label,
                               face_colors=np.vstack(colours), views=("изометрия",), tile=520,
                               frame=frame, arrows=(arrow,) if arrow else ())
            return Image.open(BytesIO(png)).convert("RGB")

        out_dir = pr.root / "output" / "guide"
        out_dir.mkdir(parents=True, exist_ok=True)
        for stale in out_dir.glob("step-*.png"):       # шагов могло стать меньше
            stale.unlink()
        heading = title.strip() or str(pr.manifest.get("name") or pr.root.name)
        steps: list[dict] = []
        lines = [f"инструкция «{heading}»: шагов {len(order)}"]
        for k, name in enumerate(order):
            step = pr.assembly.steps.get(name)
            joint = pr.assembly.joints.get(name)
            text = _step_text(k + 1, name, step, pr.parts[name].printed, joint)
            tiles = []
            if name in lifts:
                v = meshes[name][0]
                centre = (v.min(0) + v.max(0)) / 2
                arrow = (tuple(centre + lifts[name]), tuple(centre), step.insert[0].upper())
                tiles.append(picture(order[:k], name, lifts[name], f"шаг {k + 1}: до", arrow))
            tiles.append(picture(order[:k], name, None,
                                 f"шаг {k + 1}: {'на месте' if k else 'основание'}", None))
            sheet = Image.new("RGB", (sum(t.width for t in tiles), max(t.height for t in tiles)),
                              (24, 26, 31))
            x = 0
            for t in tiles:
                sheet.paste(t, (x, 0))
                x += t.width
            file = f"step-{k + 1:02d}.png"
            buf = BytesIO()
            sheet.save(buf, "PNG", optimize=True)
            _atomic_bytes(out_dir / file, lambda p, data=buf.getvalue(): p.write_bytes(data))
            rel = str((out_dir / file).relative_to(pr.root))
            steps.append({"n": k + 1, "part": name, "text": text, "file": file,
                          "method": step.method if step and k else "",
                          "insert": list(step.insert) if step else [],
                          "image": {"path": rel}})
            lines.append(f"  {k + 1}. {name}: {text}")
        unlisted = sorted(pr.assembly.unlisted(list(pr.parts)))
        page = out_dir / "index.html"
        _atomic_bytes(page, lambda p: p.write_bytes(_page(heading, steps, unlisted).encode("utf-8")))
        rel_page = str(page.relative_to(pr.root))
        lines.append(f"страница: {rel_page} (картинки рядом, открывается без сети)")
        if unlisted:
            lines.append(f"не в порядке сборки, шага нет: {', '.join(unlisted)}")
        lines.append("Крепежа (винты, клей) в инструкции нет: Datum знает только детали "
                     "порядка — допишите его в текст страницы или объявите деталями.")
        return {"title": heading, "steps": [{k: v for k, v in s.items() if k != "file"}
                                            for s in steps],
                "page": rel_page, "unlisted": unlisted, "report": "\n".join(lines)}

    return _guard(run)
