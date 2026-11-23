"""Операции опыта печати — см. `doc/lessons.py`."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from datum.tools.ops import Project, _err, _guard, open_project, project_root_only
from datum.tools.registry import Arg, op

__all__ = ["print_log", "lessons_list"]

Outcome = Literal["плохо", "хорошо", "заметка"]


@op("print_log", cli_name="print-log", group="проект", isolate=True, danger="mutates",
    title="Записать итог печати",
    summary=("Записать, что напечатали и чем кончилось: строкой в журнал проекта "
             "(JOURNAL.md) и уроком в ОБЩУЮ базу опыта печати — она на все проекты. "
             "sign — признак позы детали на столе (vertex_down — стоит на вершине, "
             "edge_down — на ребре, small_footprint, tall_thin): с ним сборка любой "
             "будущей детали с тем же признаком напомнит об уроке. Признак принимается, "
             "только если выполняется на самой детали урока. Пишите слова человека, а "
             "причину — только установленную: догадка в базе станет «знанием»."))
async def print_log(
    project: Project,
    outcome: Annotated[Outcome, Arg(help="плохо — брак или дефект, хорошо — вышло, "
                                         "заметка — наблюдение")],
    text: Annotated[str, Arg(help="что напечатали и чем кончилось, одной-двумя фразами")],
    part: Annotated[str, Arg(help="деталь, о которой урок; пусто — про проект целиком")] = "",
    sign: Annotated[str, Arg(help="признак позы: vertex_down, edge_down, small_footprint, "
                                  "tall_thin; пусто — без признака")] = "",
    sign_args: Annotated[dict[str, str] | None,
                         Arg(help="порог признака: {\"max_mm2\": \"60\"}, {\"ratio\": \"6\"}")] = None,
) -> dict[str, Any]:
    def run():
        from datum.doc import journal, lessons
        from datum.io.export import on_bed

        pr = open_project(project)
        printer = pr.manifest.get("printer", {})
        up, measured = "", ""
        if part:
            if part not in pr.parts:
                return _err(f"нет детали {part!r}; есть: {', '.join(sorted(pr.parts))}")
            up = pr.parts[part].print_up or ""
        if sign:
            if not part:
                return _err("признак — про позу детали на столе: назовите деталь (part)")
            built = pr.build([part])[part]
            if not built.ok:
                return _err(built.line())
            try:
                holds, measured = lessons.sign_holds(sign, sign_args,
                                                     on_bed(built.solid, up, pr.bed))
            except lessons.LessonError as e:
                return _err(str(e))
            if not holds:
                return _err(
                    f"признак {sign} ({lessons.describe_sign(sign)}) НЕ выполняется на самой "
                    f"детали {part}: {measured}. Урок с таким признаком напоминал бы о себе "
                    f"не там. Деталь с тех пор перевернули или изменили? Тогда запишите урок "
                    f"без признака или подберите порог (sign_args)")
        try:
            lesson = lessons.add(project=pr.name, part=part, outcome=outcome, text=text,
                                 material=str(printer.get("material", "")),
                                 printer=str(printer.get("profile", "")), print_up=up,
                                 sign=sign, args=sign_args)
            journal.add(pr.root, "печать", (f"{part}: " if part else "") + f"[{outcome}] "
                        + lesson.text)
        except (lessons.LessonError, journal.JournalError) as e:
            return _err(str(e))
        lines = [f"записано: {lesson.line()}",
                 f"в журнал проекта ({journal.FILE}) и в общую базу опыта ({lessons.path()})"]
        if sign:
            lines.append(f"признак: {lessons.describe_sign(sign)} — {measured}. Сборка "
                         f"любой детали с тем же признаком напомнит об этом уроке")
        else:
            lines.append("без признака: урок виден в сводке проектов (project_brief) и в "
                         "lessons_list, сам о себе на сборке не напомнит")
        return {"lesson": {"id": lesson.id, "sign": lesson.sign, "args": lesson.args},
                "report": "\n".join(lines)}

    return _guard(run)


@op("lessons_list", cli_name="lessons", group="проект",
    title="Опыт печати",
    summary=("Уроки общей базы опыта печати (на все проекты): что печатали, из чего, "
             "чем кончилось, с каким признаком. Смотрите перед выбором позы печати и "
             "перед долгой печатью."))
async def lessons_list(
    project: Project,
    match: Annotated[str, Arg(help="подстрока: пластик, проект, слово из урока; пусто — все")] = "",
) -> dict[str, Any]:
    def run():
        from datum.doc import lessons

        project_root_only(project)
        found = lessons.read()
        needle = match.strip().lower()
        if needle:
            found = [x for x in found if needle in
                     f"{x.line()} {x.sign} {lessons.describe_sign(x.sign)}".lower()]
        if not found:
            return {"lessons": [], "report": (
                "в базе опыта печати ничего нет" + (f" по «{match}»" if needle else "")
                + f" ({lessons.path()}). Итог печати записывает print_log")}
        lines = [f"опыт печати — {len(found)}:"]
        for x in found[-40:]:
            lines.append("  " + x.line()
                         + (f" [признак: {lessons.describe_sign(x.sign)}]" if x.sign else ""))
        if len(found) > 40:
            lines.insert(1, f"  (показаны последние 40 из {len(found)}; сузьте match)")
        return {"lessons": [{"id": x.id, "date": x.date, "project": x.project, "part": x.part,
                             "outcome": x.outcome, "text": x.text, "material": x.material,
                             "sign": x.sign} for x in found[-40:]],
                "report": "\n".join(lines)}

    return _guard(run)
