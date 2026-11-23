"""Критик и окно: вердикт, отчёт окна, показ человеку, вид стола.

Часть операций Datum (`tools/ops.py` — их общий вход: оттуда строятся
MCP-инструменты и команды CLI). Соглашение то же: операция возвращает
словарь, `report` — текст для человека, об ошибке — ключ `error`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from datum.tools.ops_base import Plane, Project, _err, _guard, open_project, project_root_only
from datum.tools.ops_models import FOREIGN_MESH_HEADROOM_MB
from datum.tools.registry import Arg, op


# ------------------------------------------------------------------ критик
@op("critic_verdict", cli_name="critic", group="проект", danger="mutates",
    title="Вердикт ИИ-критика",
    summary=("Записать итог независимого разбора проекта субагентом-критиком: "
             "«принято» или «доработать» и замечания, каждое с доводом (проверка, "
             "число, сечение). Ответ говорит, какой это круг из двух и что делать "
             "главному агенту. Зовёт критик, а не главный агент."))
async def critic_verdict(
    project: Project,
    verdict: Annotated[Literal["принято", "доработать"],
                       Arg(help="принято — настоящих недочётов нет; доработать — есть")],
    remarks: Annotated[list[str] | None, Arg(help="замечания: что не так, довод, что "
                                                   "сделать; одно на строку",
                                              metavar="ЗАМЕЧАНИЕ")] = None,
    summary: Annotated[str, Arg(help="итог одной-двумя фразами")] = "",
) -> dict[str, Any]:
    from datum.doc.critic import record

    def run():
        root = project_root_only(project)
        notes = [r.strip() for r in (remarks or []) if r and r.strip()]
        if verdict == "доработать" and not notes:
            return _err("«доработать» без замечаний ничего не говорит главному агенту — "
                        "перечислите, что не так и почему")
        done = record(root, verdict, notes, summary)
        lines = [f"Критик, взгляд {done['review']} из {done['max_reviews']}: {verdict.upper()}"]
        if summary:
            lines.append(f"  {summary}")
        lines += [f"  {i}. {note}" for i, note in enumerate(notes, 1)]
        lines.append(done["advice"])
        return {**done, "verdict": verdict, "remarks": notes, "report": "\n".join(lines)}

    return _guard(run)


# ------------------------------------------------------------------ окно и стол
@op("window_report", cli=False, mcp=False,
    title="Окно сообщает, что показывает",
    summary=("Записать, что сейчас видит человек в окне: выделение, скрытое, срез, "
             "разнесение, камеру, выбранное штангенциркулем и последний замер. Зовёт "
             "только окно; агенту операция не показывается и от него не принимается."))
async def window_report(
    project: Project,
    state: Annotated[dict, Arg(help="состояние окна (см. datum.doc.window)")],
) -> dict[str, Any]:
    from datum.doc import window as W

    def run():
        root = Path(project).resolve()
        if not (root / "datum.toml").is_file():
            return _err(f"{root} — не проект Datum")
        try:
            clean = W.sanitize(state)
        except W.WindowStateError as e:
            return _err(str(e))
        W.save(root, clean)
        return {"ok": True}

    # Пишет только .datum/window.json и кода проекта не исполняет: изоляции не
    # нужно, а процесс сервера — тот, чья жизнь и значит «окно открыто».
    return _guard(run)


@op("window_state", cli_name="window", group="проект",
    title="Что сейчас в окне",
    summary=("Что видит человек в окне Datum: выделенная деталь, скрытые детали и "
             "образцы, срез, разнесение, откуда смотрит камера, что выбрано "
             "штангенциркулем и какой замер показан. Звать, когда человек говорит "
             "«эта деталь», «вот здесь», «мой замер». Числа замера — показанные окном; "
             "перемерить можно measure_probe, section, measure_parts."))
async def window_state(project: Project) -> dict[str, Any]:
    from datum.doc import window as W

    def run():
        root = Path(project).resolve()
        state = W.load(root)
        return {"window": state, "report": W.describe(state)}

    return _guard(run)


#: Строка агента поверх окна — коротко: это подпись к виду, а не письмо.
SHOW_NOTE_MAX = 300


@op("window_show", cli_name="show", group="проект",
    title="Показать человеку в окне",
    summary=("Показать человеку в открытом окне то, о чём идёт речь: навести камеру и "
             "выделить детали (parts), спрятать остальные (isolate), включить срез "
             "(plane, at), поставить звено шарнира (joint, joint_u) и подписать вид "
             "строкой (note). Меняется ВИД, а не проект: человек вернёт прежний одной "
             "кнопкой «Вернуть как было». Окно закрыто — отказ: показывать некому."))
async def window_show(
    project: Project,
    parts: Annotated[list[str] | None, Arg(help="детали, на которые навести камеру и выделить")] = None,
    isolate: Annotated[bool, Arg(help="спрятать все остальные детали")] = False,
    plane: Annotated[Plane | None, Arg(help="срез: xy, xz или yz")] = None,
    at: Annotated[float | None, Arg(help="координата среза, мм")] = None,
    joint: Annotated[str, Arg(help="звено шарнира из assembly.yaml")] = "",
    joint_u: Annotated[float | None, Arg(help="положение звена: градусы или мм")] = None,
    note: Annotated[str, Arg(help="что сказать человеку — строка поверх окна")] = "",
) -> dict[str, Any]:
    """Показ — это ВИД: камера, выделение, скрытое, срез, поза шарнира. Проект
    не меняется, и «что видит человек» не подделывается: окно сообщит новый вид
    своим `window_report`, как после щелчка человека.

    Срез и сдвинутое звено вместе не показываются: окно гасит срез, когда звено
    не на месте (крышка среза считается по собранной геометрии), — поэтому
    отказ здесь, а не молча половина показа.
    """
    from datum.doc import window as W

    def run():
        # Проект здесь НЕ открывается: открытие исполняет скрипты деталей, а
        # операция идёт в процессе сервера (изолировать её нельзя — ребёнку
        # воркера запрещена сеть, а пересылать серверу ей нужно). Детали —
        # из того, что окно само загрузило и сообщило; шарниры — из YAML.
        root = Path(project).resolve()
        if not (root / "datum.toml").is_file():
            return _err(f"{root} — не проект Datum: нет datum.toml")
        wanted = list(parts or [])
        if isolate and not wanted:
            return _err("isolate прячет всё, кроме parts, — а parts не названы")
        if (plane is None) != (at is None):
            return _err("срез задаётся парой: plane и at")
        if (joint == "") != (joint_u is None):
            return _err("шарнир задаётся парой: joint и joint_u")
        if joint and plane is not None and joint_u != 0:
            return _err("срез и сдвинутое звено вместе не показываются: окно гасит срез, "
                        "когда звено не на месте. Покажите по очереди")
        if len(note) > SHOW_NOTE_MAX:
            return _err(f"note длиннее {SHOW_NOTE_MAX} знаков — это подпись к виду; "
                        f"остальное скажите в ответе")
        if not (wanted or plane or joint or note):
            return _err("показывать нечего: назовите parts, срез, шарнир или note")

        if not W.SERVING.get():
            # Вызов пришёл мимо сервера окна (CLI, свой процесс MCP): событие
            # отсюда до окна не дошло бы — пересылаем тому, кто его держит.
            from datum.tools.remote import _post, find_endpoint

            endpoint = find_endpoint(str(root))
            if endpoint is None:
                return _err("окно этого проекта не открыто — показать некому. "
                            "Откройте окно (datum <проект>) или опишите словами")
            payload = {k: v for k, v in dict(project=str(root), parts=wanted or None,
                                             isolate=isolate, plane=plane, at=at,
                                             joint=joint, joint_u=joint_u,
                                             note=note).items() if v not in (None, "")}
            return _post(endpoint, "window_show", payload)

        # Сервер жив, но окно ни разу не сообщило о себе (`datum serve` без
        # окна) — событие ушло бы в пустоту, и «показано» было бы неправдой.
        seen = W.load(root)
        if not seen or not seen.get("open"):
            return _err("окно этого проекта не открыто — показать некому")
        loaded = seen.get("parts")
        if loaded is not None:
            unknown = [n for n in wanted if n not in loaded]
            if unknown:
                from datum.build.hints import names_hint

                tip = names_hint(unknown, loaded)
                return _err(f"в окне нет деталей {unknown}" + (f" ({tip})" if tip else "")
                            + f"; есть: {', '.join(sorted(loaded))}")
        pose = None
        if joint:
            from datum.doc.assembly import Assembly, AssemblyError

            path = root / "assembly.yaml"
            try:
                joints = Assembly.load(path).joints if path.is_file() else {}
            except AssemblyError as e:
                return _err(str(e))
            if joint not in joints:
                return _err(f"шарнира {joint!r} нет в assembly.yaml"
                            + (f"; есть: {', '.join(sorted(joints))}" if joints
                               else " — joints: не объявлены"))
            lo, hi = joints[joint].limits
            lo, hi = min(0.0, lo), max(0.0, hi)              # как ползунок окна
            if not lo <= joint_u <= hi:
                return _err(f"{joint}: положение {joint_u:g} — вне хода {lo:g}…{hi:g}")
            pose = {"part": joint, "u": joint_u}

        show = {"parts": wanted, "isolate": isolate,
                "section": {"plane": plane, "at": at} if plane else None,
                "joint": pose, "note": note}
        said = [f"детали {', '.join(wanted)}" + (" (остальные спрятаны)" if isolate else "")
                if wanted else "",
                f"срез по {dict(xy='Z', xz='Y', yz='X')[plane]} = {at:g} мм" if plane else "",
                f"шарнир {joint} в положении {joint_u:g}" if pose else "",
                f"«{note}»" if note else ""]
        import time

        # Время показа: окно не применяет старый показ, досланный повтором
        # после переподключения (`viewport/show.ts::SHOW_FRESH_S`).
        return {"shown": show, "events": [{"t": "window.show", "at": time.time(), **show}],
                "report": "Показано в окне: " + "; ".join(x for x in said if x)
                          + ". Человек вернёт прежний вид кнопкой «Вернуть как было»."}

    return _guard(run)


@op("scene_view", cli_name="scene", group="замеры",
    isolate=True, memory_headroom_mb=FOREIGN_MESH_HEADROOM_MB,
    title="Вид стола в картинке",
    summary=("Нарисовать весь стол — все детали на своих местах (с положением из "
             "placement.yaml) и образцы импорта — с четырёх сторон и, если окно "
             "открыто, ещё и оттуда, откуда смотрит человек. Скрытые в окне детали "
             "не рисуются. Агенту картинка приходит в ответе, путь — там же. "
             "bed=True — в рамке всего стола, чтобы видеть, где детали на нём. "
             "Зелёные стрелки — направление вставки из assembly.yaml: покажите "
             "человеку позу ДО геометрии."))
async def scene_view(
    project: Project,
    bed: Annotated[bool, Arg(help="показать весь стол, а не только детали")] = False,
    ghosts: Annotated[bool, Arg(help="рисовать образцы импорта")] = True,
    arrows: Annotated[bool, Arg(help="стрелки вставки из assembly.yaml")] = True,
) -> dict[str, Any]:
    import numpy as np

    from datum.build.imported import imported_names, result_path, used_by
    from datum.checks.motion import direction as parse_direction
    from datum.doc import window as W
    from datum.geom.convert import vertices_faces
    from datum.geom.repair import load_mesh
    from datum.viz.render import render_views

    ghost_color = "#9ccaff"                            # как образец в окне
    bed_color = "#2a3140"

    def run():
        pr = open_project(project)
        seen = W.load(pr.root)
        hidden = set(seen["hidden"]) if seen else set()
        shown = [n for n in pr.parts if n not in hidden]
        # Пустой список у build — «все детали»: скрыто всё — собирать нечего.
        results = pr.build(shown) if shown else {}
        verts, faces, colors, creases, legend, lines, offset = [], [], [], [], [], [], 0

        def add(v, f, color: str, edges: bool = True):
            nonlocal offset
            verts.append(np.asarray(v, float))
            faces.append(np.asarray(f, np.int64) + offset)
            rgb = [int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
            colors.append(np.tile(rgb, (len(f), 1)))
            creases.append(np.full(len(f), edges))
            offset += len(v)

        for name, r in results.items():
            if not r.ok:
                lines.append(f"  {name}: не собралась — {r.line()}")
                continue
            color = pr.parts[name].color or "#9aa4b2"
            v, f = vertices_faces(r.solid.manifold)
            add(v, f, color)
            legend.append((name, color))
            lo, hi = v.min(0), v.max(0)
            lines.append(f"  {name}: X {lo[0]:.1f}…{hi[0]:.1f}, Y {lo[1]:.1f}…{hi[1]:.1f}, "
                         f"Z {lo[2]:.1f}…{hi[2]:.1f} мм")
        # Образцы — как в окне: сказанное окном, а без окна — те, что никто не
        # берёт (взятую скриптом модель показывает её деталь).
        shown_ghosts = []
        if ghosts:
            wanted = set(seen["ghosts"]) if seen else {
                m for m in imported_names(pr.root) if not used_by(pr.root, m)}
            for model in imported_names(pr.root):
                path = result_path(pr.root, model)
                if model not in wanted or path is None:
                    continue
                mesh = load_mesh(path)
                # Образец — без изломов, одним силуэтом: как в окне, где он
                # полупрозрачный и без рёбер.
                add(mesh.vertices, mesh.faces, ghost_color, edges=False)
                shown_ghosts.append(model)
                lines.append(f"  образец {model}: {len(mesh.faces):,} треуг.")
            if shown_ghosts:
                legend.append(("образцы", ghost_color))
        if not faces:
            return _err("рисовать нечего: в проекте нет собранных деталей"
                        + (" (все скрыты в окне)" if hidden else ""))
        if bed:
            bx, by, _ = pr.bed
            add([[0, 0, -0.05], [bx, 0, -0.05], [bx, by, -0.05], [0, by, -0.05]],
                [[0, 1, 2], [0, 2, 3]], bed_color, edges=False)
            legend.append(("стол", bed_color))

        views = ["изометрия", "спереди", "справа", "сверху"]
        cameras = {}
        cam = seen.get("camera") if seen and seen.get("open") else None
        if cam:
            toward = np.subtract(cam["position"], cam["target"])
            cameras["из окна"] = (tuple(toward / np.linalg.norm(toward)), tuple(cam["up"]))
            views.append("из окна")
        notes = []
        if hidden:
            notes.append(f"скрыты в окне: {', '.join(sorted(hidden))}")
        if seen and seen.get("spread"):
            notes.append("в окне вид разнесён, здесь — детали на своих местах")
        if seen and seen.get("joints"):
            shown = ", ".join(f"{k} {v:g}" for k, v in sorted(seen["joints"].items()))
            notes.append(f"в окне шарниры не в собранном положении ({shown}), здесь — в собранном")
        # Выбранное штангенциркулем — отметками 1 и 2 на всех видах: агент
        # видит, что именно намерил человек.
        marks = []
        for i, e in enumerate((seen.get("picked") or []) if cam else [], 1):
            anchor = {"point": lambda: e["point"], "circle": lambda: e["centre"],
                      "plane": lambda: e["point"],
                      "edge": lambda: [(p + q) / 2 for p, q in zip(e["a"], e["b"])]}[e["type"]]()
            marks.append((tuple(anchor), f"{i} {e['kind']}"))
        if marks:
            notes.append("оранжевым — выбранное в окне штангенциркулем")
        # Стрелки вставки — замысел из assembly.yaml на картинке: «предмет в
        # стойке, стрелка, стол» показывают человеку ДО геометрии. Ошибку позы
        # (стойка горизонтальная вместо вертикальной) так ловят за минуту.
        pointers = []
        for name, r in results.items():
            step = pr.assembly.steps.get(name) if arrows else None
            if not (r.ok and step is not None and step.insert):
                continue
            v, _ = vertices_faces(r.solid.manifold)
            lo, hi = v.min(0), v.max(0)
            centre = (lo + hi) / 2
            reach = 0.6 * float(np.max(hi - lo))
            for key in step.insert:
                d = np.asarray(parse_direction(key), float)
                d = d / np.linalg.norm(d)
                pointers.append((tuple(centre - d * reach), tuple(centre), f"{name} {key}"))
                lines.append(f"  {name}: ставится {key} ({step.method})")
        if pointers:
            notes.append("зелёным — куда деталь движется, становясь на место")
        png = render_views(np.concatenate(verts), np.concatenate(faces), title="стол",
                           note="; ".join(notes), face_colors=np.concatenate(colors),
                           legend=tuple(legend), views=tuple(views), cameras=cameras,
                           creases=np.concatenate(creases), marks=tuple(marks),
                           arrows=tuple(pointers))
        digest = pr.cache.put_blob(png, "png", kind="view", meta={"scene": True, "bed": bed})
        blob = pr.cache.blob_path(digest, "png")
        path = (str(blob.relative_to(pr.root))
                if blob is not None and blob.is_relative_to(pr.root) else str(blob))
        head = f"стол: {len(results)} дет." + (f", {len(shown_ghosts)} образц." if shown_ghosts else "")
        if cam:
            head += "; последний вид — оттуда, откуда смотрит окно"
        return {"image": {"url": f"/obj/{digest}.png", "path": path},
                "parts": sorted(n for n, r in results.items() if r.ok),
                "hidden": sorted(hidden), "ghosts": shown_ghosts,
                "report": "\n".join([head, *lines, *(f"  {n}" for n in notes),
                                     f"картинка: {path}"])}

    return _guard(run)
