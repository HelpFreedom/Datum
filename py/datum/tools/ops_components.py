"""Операции общей библиотеки покупного — см. `doc/components.py`.

Проект они не открывают (`project_root_only`): работа — с текстом
`params/base.yaml` и файлами, скрипты деталей не нужны, а неизолированной
операции исполнять код проекта нельзя.
"""

from __future__ import annotations

from typing import Annotated, Any

from datum.tools.ops import Project, _err, _guard, project_root_only
from datum.tools.ops_params import _checked
from datum.tools.registry import Arg, op

__all__ = ["component_list", "component_save", "component_use"]


@op("component_list", cli_name="components", group="проект",
    title="Библиотека покупного",
    summary=("Что лежит в общей библиотеке покупных деталей (моторы, сервы, подшипники, "
             "крепёж): имя, что это, какие параметры и файлы несёт, из какого проекта и "
             "когда сохранено. Смотрите сюда ПРЕЖДЕ, чем обмерять покупную вещь или "
             "переписывать её размеры из другого проекта."))
async def component_list(
    project: Project,
    match: Annotated[str, Arg(help="подстрока имени или описания; пусто — все")] = "",
) -> dict[str, Any]:
    def run():
        from datum.doc import components as C

        project_root_only(project)
        found = C.listing()
        needle = match.strip().lower()
        if needle:
            found = [c for c in found
                     if needle in f"{c['name']} {c.get('title', '')} {c.get('note', '')}".lower()]
        if not found:
            return {"components": [], "library": str(C.library_root()),
                    "report": ("в библиотеке ничего нет" if not needle else
                               f"в библиотеке нет ничего по «{match}»")
                    + f" ({C.library_root()}). Обмеренную покупную вещь кладут туда "
                      f"component_save — в следующем проекте её не придётся мерить заново"}
        lines = [f"библиотека покупного — {len(found)} ({C.library_root()}):"]
        for c in found:
            if c.get("broken"):
                lines.append(f"  {c['name']}: запись испорчена (component.json не читается)")
                continue
            lines.append(f"  {c['name']}" + (f" — {c['title']}" if c["title"] else "")
                         + f": параметров {len(c['params'])}"
                         + (f", файлы {', '.join(c['files'])}" if c["files"] else "")
                         + f"; из «{c['project']}», {c['saved']}")
            if c["note"]:
                lines.append(f"      {c['note']}")
            if c["params"]:
                shown = c["params"][:12]
                lines.append("      " + ", ".join(shown)
                             + (f" … и ещё {len(c['params']) - 12}" if len(c["params"]) > 12 else ""))
        lines.append("взять в проект — component_use(name=…): параметры и файлы копией, "
                     "с пометкой, откуда")
        return {"components": found, "library": str(C.library_root()),
                "report": "\n".join(lines)}

    return _guard(run)


@op("component_save", cli_name="component-save", group="проект", danger="mutates",
    title="Сохранить покупную деталь в библиотеку",
    summary=("Положить покупную вещь из этого проекта в ОБЩУЮ библиотеку (вне проекта, "
             "на все проекты): её параметры — текстом, с происхождением и источником "
             "замера — и файлы, которые с ними ходят (скрипт макета в lib/, таблица в "
             "data/). Параметры — именами (params) и/или началом имени (prefix=\"MOTOR_\"). "
             "Формула обязана читать только параметры компонента. Сохраняйте то, что "
             "ЗАМЕРЕНО или взято из паспорта: догадку в библиотеку не кладут."))
async def component_save(
    project: Project,
    name: Annotated[str, Arg(help="имя компонента — как вещь называется: 5840-31ZY, MG996R")],
    params: Annotated[list[str] | None, Arg(help="параметры по именам", metavar="ИМЯ")] = None,
    prefix: Annotated[str, Arg(help="взять все параметры, имя которых начинается так")] = "",
    files: Annotated[list[str] | None,
                     Arg(help="файлы проекта из lib/ и data/, которые идут с компонентом",
                         metavar="ФАЙЛ")] = None,
    title: Annotated[str, Arg(help="что это, одной строкой: «мотор-редуктор 24 В с "
                                   "двойным валом»")] = "",
    note: Annotated[str, Arg(help="что важно знать: чем мерили, какая партия, что не "
                                  "замерено")] = "",
    replace: Annotated[bool, Arg(help="заменить компонент с этим именем")] = False,
) -> dict[str, Any]:
    def run():
        import re

        from datum.doc import components as C
        from datum.doc.params import ParamRegistry

        root = project_root_only(project)
        names = list(dict.fromkeys(params or []))
        path = root / "params" / "base.yaml"
        reg = ParamRegistry.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        if prefix.strip():
            if reg is None:
                return _err("в проекте нет params/base.yaml")
            taken = [p.name for p in reg if p.name.startswith(prefix.strip())]
            if not taken:
                return _err(f"нет параметров, начинающихся с {prefix!r}")
            names += [n for n in taken if n not in names]
        try:
            comp = C.save(root, name, params=names, files=list(files or []), title=title,
                          note=note, replace=replace)
        except C.ComponentError as e:
            return _err(str(e))
        lines = [f"компонент {comp.name!r} сохранён в библиотеку: параметров "
                 f"{len(comp.params)}, файлов {len(comp.files)} ({C.library_root() / comp.name})"]
        # Догадка в библиотеке станет «фактом» следующего проекта — назвать.
        guessed = [p.name for p in reg if p.name in comp.params and p.provenance == "assumed"] \
            if reg is not None else []
        if guessed:
            lines.append(f"! среди них УГАДАННЫЕ (assumed): {', '.join(guessed)} — в другом "
                         f"проекте они останутся догадкой с тем же диапазоном; замерьте, "
                         f"прежде чем полагаться")
        # Скрипт, читающий параметр, которого в компоненте нет, в другом проекте упадёт.
        read: set[str] = set()
        for rel, content in comp.files.items():
            if rel.endswith(".py"):
                read |= set(re.findall(r"\bP\.([A-Za-z_][A-Za-z0-9_]*)",
                                       content.decode("utf-8", "replace")))
        lost = sorted(n for n in read - set(comp.params) if not n.startswith("MAT_"))
        if lost:
            lines.append(f"! файлы читают параметры, которых нет в компоненте: "
                         f"{', '.join(lost)} — в другом проекте их придётся объявить")
        return {"name": comp.name, "params": list(comp.params), "files": sorted(comp.files),
                "assumed": guessed, "unbound": lost, "report": "\n".join(lines)}

    return _guard(run)


@op("component_use", cli_name="component-use", group="проект", danger="mutates",
    title="Взять покупную деталь из библиотеки",
    summary=("Положить компонент из общей библиотеки в проект КОПИЕЙ: его параметры "
             "дописываются в params/base.yaml с прежним происхождением и пометкой, откуда "
             "взяты, файлы — в lib/ и data/. Параметр, который в проекте уже есть, не "
             "трогается (отличие называется); свой файл с тем же именем и другим "
             "содержимым — отказ. Проект остаётся самодостаточным."))
async def component_use(
    project: Project,
    name: Annotated[str, Arg(help="имя компонента (component_list)")],
) -> dict[str, Any]:
    def run():
        from datum.doc import components as C

        root = project_root_only(project)
        try:
            comp = C.load(name)
            done = C.use(root, comp, lambda text: _checked(root, text))
        except C.ComponentError as e:
            return _err(str(e))
        lines = [f"компонент {comp.name!r}" + (f" — {comp.title}" if comp.title else "")
                 + f" (из «{comp.project}», {comp.saved}):"]
        if done.added:
            lines.append(f"  дописано параметров {len(done.added)}: {', '.join(done.added)}")
        if done.same:
            lines.append(f"  уже были с тем же значением: {', '.join(done.same)}")
        for pname, mine, theirs in done.differ:
            lines.append(f"  ! {pname}: в проекте {mine}, в библиотеке {theirs} — проект НЕ "
                         f"тронут. Чьё число верно, решите с человеком (param_set с source)")
        if done.files_written:
            lines.append(f"  файлы: {', '.join(done.files_written)}")
        if done.files_same:
            lines.append(f"  файлы уже были те же: {', '.join(done.files_same)}")
        if comp.note:
            lines.append(f"  заметка: {comp.note}")
        if not (done.added or done.files_written):
            lines.append("  в проект ничего не записано: всё уже на месте")
        lines.append("Деталь объявляет проект: @part(…, printed=False) со своим скриптом, "
                     "который берёт эти параметры (и функцию макета из lib/, если она есть).")
        return {"name": comp.name, "added": done.added, "same": done.same,
                "differ": [{"param": p, "project": a, "library": b}
                           for p, a, b in done.differ],
                "files": done.files_written, "report": "\n".join(lines)}

    return _guard(run)
