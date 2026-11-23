"""Правка реестра параметров сверх одного значения: новый параметр, поля
существующего, несколько значений разом.

Зачем отдельно от `param_set`. Разбор сессий агента (аудит 2026-11-21): в одной
сессии `params/base.yaml` тронут из Bash 159 раз — `cat >>`, `sed -i`, свой
Python. Причина простая: `param_set` умеет только менять значение, а добавить
параметр, поправить причину, диапазон или формулу было нечем. Правка мимо
операции — это правка мимо правил реестра: её никто не перечитывал, и опечатка
всплывала на следующей сборке отказом всего проекта.

Правила те же, что у `param_set`, и держит их сам реестр: после каждой правки
файл разбирается заново ЦЕЛИКОМ (`ParamRegistry.loads` + `resolve` — с
параметрами материала, как при открытии проекта), и запись, которую реестр не
принял, на диск не попадает. Правка — текстом по месту (`doc/params_edit.py`):
комментарии, в которых у проекта живёт рассуждение, остаются.

Проект операции не открывают (`project_root_only`): скрипты деталей им не
нужны, а неизолированной операции исполнять код проекта нельзя.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from datum.tools.ops import Project, _err, _guard, _material_catalog_params, project_root_only
from datum.tools.registry import Arg, op

__all__ = ["param_add", "param_edit", "param_set_many", "note_add", "project_brief"]

Provenance = Literal["chosen", "assumed", "standard", "measured", "derived"]


def _load(project: str) -> tuple[Path, Path, str]:
    root = project_root_only(project)
    path = root / "params" / "base.yaml"
    if not path.is_file():
        raise ValueError(f"в проекте нет params/base.yaml: {root}")
    return root, path, path.read_text(encoding="utf-8")


def _checked(root: Path, text: str):
    """Реестр из текста — таким, каким его увидит проект, или отказ словами.

    `resolve` обязателен: формула с опечаткой в имени разбирается без ошибки и
    падает только при вычислении — то есть на следующей сборке.
    """
    from datum.doc.params import ParamRegistry

    reg = ParamRegistry.loads(text).with_defaults(_material_catalog_params(root))
    reg.resolve()
    return reg


def _line(p) -> str:
    d = p.as_dict()
    value = d.get("value", d.get("expr", "?"))
    span = f" [{d['range'][0]:g}…{d['range'][1]:g}]" if d.get("range") else ""
    return f"{p.name} = {value} {p.unit}{span} ({p.provenance})".replace("  ", " ")


@op("param_add", cli_name="param-add", group="проект", danger="mutates",
    title="Добавить параметр",
    summary=("Объявить новый параметр в params/base.yaml — строкой, по правилам "
             "реестра: chosen и standard — значение (standard — ещё и ref), "
             "assumed — значение с диапазоном range, measured — значение с source, "
             "derived — формула expr без значения. Не правьте YAML руками: запись, "
             "которую реестр не принял, на диск не попадёт."))
async def param_add(
    project: Project,
    name: Annotated[str, Arg(help="имя нового параметра (ЗАГЛАВНЫМИ_ЛАТИНИЦЕЙ)")],
    provenance: Annotated[Provenance, Arg(help="происхождение числа")],
    unit: Annotated[str, Arg(help="единица: mm, deg, N, MPa, g…; пусто — безразмерное")] = "",
    value: Annotated[float | None, Arg(help="значение в единицах параметра; у derived нет")] = None,
    expr: Annotated[str, Arg(help="формула производного (derived): WALL * 2 + GAP")] = "",
    range: Annotated[list[float] | None,
                     Arg(help="диапазон догадки (assumed): низ верх", metavar="ЧИСЛО")] = None,
    reason: Annotated[str, Arg(help="почему такое значение (chosen)")] = "",
    ref: Annotated[str, Arg(help="откуда число: стандарт, паспорт, страница (standard)")] = "",
    source: Annotated[str, Arg(help="чем и как измерено (measured)")] = "",
    measure_hint: Annotated[str, Arg(help="как это замерить (assumed)")] = "",
    blocking: Annotated[bool, Arg(help="догадка, от которой зависит печать (assumed)")] = False,
    note: Annotated[str, Arg(help="заметка")] = "",
    after: Annotated[str, Arg(help="поставить после этого параметра; пусто — в конец файла")] = "",
) -> dict[str, Any]:
    from datum.doc.params import ParamError, ParamRegistry
    from datum.doc.params_edit import EditError, add_param, write_atomically

    def run():
        root, path, text = _load(project)
        if name in ParamRegistry.loads(text):
            return _err(f"параметр {name!r} уже есть — значение меняет param_set, "
                        f"остальное param_edit")
        if after and after not in ParamRegistry.loads(text):
            return _err(f"нет параметра {after!r}, после которого ставить")
        body: dict[str, Any] = {}
        if value is not None:
            body["value"] = float(value)
        if expr.strip():
            body["expr"] = expr.strip()
        body["unit"] = unit.strip()
        body["provenance"] = provenance
        if range:
            body["range"] = [float(v) for v in range]
        for key, text_value in (("reason", reason), ("ref", ref), ("source", source),
                                ("measure_hint", measure_hint), ("note", note)):
            if text_value.strip():
                body[key] = text_value.strip()
        if blocking:
            body["blocking"] = True
        try:
            updated = add_param(text, name, body, after=after)
            reg = _checked(root, updated)
        except (EditError, ParamError, KeyError, ValueError) as e:
            return _err(f"параметр не добавлен: {e}")
        write_atomically(path, updated)
        p = reg[name]
        return {"name": name, "param": p.as_dict(),
                "report": f"добавлен {_line(p)}"}

    return _guard(run)


@op("param_edit", cli_name="param-edit", group="проект", danger="mutates",
    title="Поправить описание параметра",
    summary=("Изменить у параметра не значение, а остальное: причину (reason), "
             "источник числа (ref), диапазон догадки (range), формулу производного "
             "(expr), подсказку к замеру, заметку, пометку blocking. Значение меняет "
             "param_set. Комментарии в файле остаются."))
async def param_edit(
    project: Project,
    name: Annotated[str, Arg(help="имя параметра")],
    expr: Annotated[str, Arg(help="новая формула (только derived)")] = "",
    range: Annotated[list[float] | None,
                     Arg(help="новый диапазон догадки: низ верх", metavar="ЧИСЛО")] = None,
    reason: Annotated[str, Arg(help="почему такое значение")] = "",
    ref: Annotated[str, Arg(help="откуда число")] = "",
    measure_hint: Annotated[str, Arg(help="как это замерить")] = "",
    note: Annotated[str, Arg(help="заметка")] = "",
    blocking: Annotated[bool | None, Arg(help="мешает ли догадка печати")] = None,
) -> dict[str, Any]:
    from datum.doc.params import ParamError, ParamRegistry
    from datum.doc.params_edit import EditError, set_fields, write_atomically

    def run():
        root, path, text = _load(project)
        reg = ParamRegistry.loads(text)
        if name not in reg:
            return _err(f"параметра {name!r} нет в params/base.yaml")
        p = reg[name]
        fields: dict[str, Any] = {}
        remove: tuple[str, ...] = ()
        if expr.strip():
            if p.provenance != "derived":
                return _err(f"{name} — {p.provenance}, формула бывает только у производного "
                            f"(derived)")
            fields["expr"] = expr.strip()
        if range:
            fields["range"] = [float(v) for v in range]
        for key, text_value in (("reason", reason), ("ref", ref),
                                ("measure_hint", measure_hint), ("note", note)):
            if text_value.strip():
                fields[key] = text_value.strip()
        if blocking is True:
            fields["blocking"] = True
        elif blocking is False and p.blocking:
            remove = ("blocking",)
        if not fields and not remove:
            return _err("нечего менять: назовите хотя бы одно поле (reason, ref, range, "
                        "expr, measure_hint, note, blocking)")
        try:
            updated = set_fields(text, name, fields, remove=remove)
            after = _checked(root, updated)[name]
        except (EditError, ParamError, KeyError, ValueError) as e:
            return _err(f"правка не легла: {e}")
        # Перечитываем то, что записали: правка текстом обязана дать ровно
        # названные значения, а не «что-то похожее».
        for key, want in fields.items():
            got = after.as_dict().get(key)
            if key == "range":
                ok = got is not None and all(abs(a - b) <= 1e-9 * max(1.0, abs(b))
                                             for a, b in zip(got, want))
            else:
                ok = got == want
            if not ok:
                return _err(f"правка не легла: после записи {name}.{key} читается как "
                            f"{got!r}, а не {want!r}")
        write_atomically(path, updated)
        changed = sorted(fields) + [f"−{k}" for k in remove]
        return {"name": name, "param": after.as_dict(), "changed": changed,
                "report": f"{_line(after)}: изменено {', '.join(changed)}"}

    return _guard(run)


@op("param_set_many", cli_name="set-many", group="проект", danger="mutates",
    title="Изменить несколько параметров",
    summary=("Задать значения нескольким параметрам разом — все или ни одного: "
             "одна неверная правка отменяет весь набор. Правила те же, что у "
             "param_set без источника (производный не правится, угаданный держится "
             "диапазона, измеренный без нового замера не меняется). Замер с "
             "источником — по одному, через param_set(source=…)."))
async def param_set_many(
    project: Project,
    values: Annotated[dict[str, float],
                      Arg(help="имя=значение в единицах параметра", metavar="ИМЯ=ЧИСЛО")],
) -> dict[str, Any]:
    from datum.doc.params import ParamError, ParamRegistry
    from datum.doc.params_edit import EditError, set_fields, write_atomically
    from datum.doc.units import from_canonical

    def run():
        root, path, text = _load(project)
        if not values:
            return _err("нечего менять: values пуст")
        reg = ParamRegistry.loads(text)
        problems: list[str] = []
        for name, value in values.items():
            if name not in reg:
                problems.append(f"{name}: такого параметра нет")
                continue
            p = reg[name]
            if p.provenance == "derived":
                problems.append(f"{name} — производный ({p.expr}): меняйте то, из чего "
                                f"он считается")
            elif p.provenance == "measured":
                problems.append(f"{name} измерен ({p.source or 'источник не записан'}): "
                                f"новое значение — только новым замером, "
                                f"param_set(source=…)")
            elif p.provenance == "assumed" and p.range:
                lo, hi = (from_canonical(v, p.unit) for v in p.range)
                if not lo - 1e-12 <= float(value) <= hi + 1e-12:
                    problems.append(f"{name}: {value:g} вне диапазона догадки "
                                    f"[{lo:g}, {hi:g}] {p.unit}")
        if problems:
            return _err("ни один параметр не изменён: " + "; ".join(problems))
        updated = text
        try:
            for name, value in values.items():
                updated = set_fields(updated, name, {"value": float(value)})
            after = _checked(root, updated)
        except (EditError, ParamError, KeyError, ValueError) as e:
            return _err(f"ни один параметр не изменён — правка не легла: {e}")
        lines, changed = [], {}
        for name, value in values.items():
            got = after[name].display
            if abs(got - float(value)) > 1e-9 * max(1.0, abs(float(value))):
                return _err(f"ни один параметр не изменён — после записи {name} читается "
                            f"как {got:g}, а не {value:g}")
            before = reg[name].display
            changed[name] = {"before": before, "after": got, "unit": reg[name].unit}
            lines.append(f"  {name}: {before:g} → {got:g} {reg[name].unit}".rstrip())
        write_atomically(path, updated)
        return {"changed": changed,
                "report": f"изменено параметров: {len(changed)}\n" + "\n".join(lines)}

    return _guard(run)


# ------------------------------------------------------------ память проекта
NoteKind = Literal["решение", "отказ", "замер", "печать", "вопрос", "этап"]


@op("note_add", cli_name="note", group="проект", danger="mutates",
    title="Записать в журнал решений",
    summary=("Одной строкой записать в JOURNAL.md проекта то, что должна знать "
             "следующая сессия: решение и почему, отвергнутый вариант и почему, "
             "замер, итог печати, законченный этап, открытый вопрос. Длинное "
             "рассуждение — в NOTES.md; сюда — вывод."))
async def note_add(
    project: Project,
    kind: Annotated[NoteKind, Arg(help="род записи")],
    text: Annotated[str, Arg(help="вывод одной-двумя фразами, до 600 знаков")],
) -> dict[str, Any]:
    from datum.doc import journal

    def run():
        root = project_root_only(project)
        try:
            entry = journal.add(root, kind, text)
        except journal.JournalError as e:
            return _err(str(e))
        return {"entry": entry.line(), "entries": len(journal.read(root)),
                "report": f"записано в {journal.FILE}: {entry.line()}"}

    return _guard(run)


@op("project_brief", cli_name="brief", group="проект",
    title="Сводка проекта",
    summary=("Что это за проект и на чём остановились — коротко, для начала новой "
             "сессии или после долгого перерыва: детали, блокирующие неизвестные, "
             "что не прошло в ПРОШЛОМ прогоне проверок, состояние выгрузки и вердикт "
             "критика, последние записи журнала решений. Кода проекта не исполняет; "
             "свежие числа дают check_run и part_build."))
async def project_brief(project: Project) -> dict[str, Any]:
    from datum.doc import brief

    def run():
        root = project_root_only(project)
        text = brief.compose(root)
        return {"chars": len(text), "report": text}

    return _guard(run)
