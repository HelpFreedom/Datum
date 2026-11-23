"""Параметры: список и значение одного параметра (остальная правка — ops_params).

Часть операций Datum (`tools/ops.py` — их общий вход: оттуда строятся
MCP-инструменты и команды CLI). Соглашение то же: операция возвращает
словарь, `report` — текст для человека, об ошибке — ключ `error`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from datum.tools.ops_base import Project, _err, _guard, open_project
from datum.tools.registry import Arg, op


@op("params_list", cli_name="params", group="проект",
    isolate=True,
    title="Параметры проекта",
    summary=("Все параметры с происхождением: что измерено, что угадано, что "
             "вычисляется. Отдельно перечисляется незамеренное и расхождения "
             "в измерениях."))
async def params_list(
    project: Project,
    only_unknown: Annotated[bool, Arg(help="только угаданные")] = False,
    match: Annotated[list[str] | None,
                     Arg(help="только параметры с такими именами или шаблонами "
                              "(H_*, *_GAP); пусто — все", metavar="ИМЯ")] = None,
) -> dict[str, Any]:
    def run():
        import fnmatch

        from datum.doc.units import from_canonical

        pr = open_project(project)
        items = pr.params.unknowns if only_unknown else list(pr.params)
        if match:
            items = [p for p in items
                     if any(fnmatch.fnmatchcase(p.name, pat) for pat in match)]
            if not items:
                return _err(f"ни один параметр не подходит под {match}; всего "
                            f"параметров {len(list(pr.params))} — params_list без match "
                            f"покажет все")
        values = pr.params.resolve()
        out = {}
        for p in items:
            d = p.as_dict()
            # У производного своего значения нет — есть формула. Но число, которое
            # идёт в геометрию, человеку нужно видеть не меньше формулы.
            if p.provenance == "derived" and p.name in values:
                d["resolved"] = round(from_canonical(values[p.name], p.unit), 6)
            out[p.name] = d
        report = pr.params.report()
        if match:
            # Отчёт реестра — про весь проект; выбранное — строкой на параметр.
            rows = []
            for name, d in out.items():
                value = d.get("resolved", d.get("value", d.get("expr", "?")))
                span = f" [{d['range'][0]:g}…{d['range'][1]:g}]" if d.get("range") else ""
                rows.append(f"  {name} = {value} {d.get('unit', '')}{span} "
                            f"({d.get('provenance', '?')})".rstrip())
            report = (f"Выбрано параметров: {len(out)} из {len(list(pr.params))}\n"
                      + "\n".join(rows))
        return {"summary": pr.params.summary(), "params": out, "report": report}

    return _guard(run)


# ------------------------------------------------------------------ сборка
@op("param_set", cli_name="set", group="проект", danger="mutates",
    title="Изменить параметр",
    summary=("Задать значение параметра в params/base.yaml — по месту, не трогая "
             "комментарии. С источником (source) угаданный параметр становится "
             "измеренным: так закрывается реестр незамеренного, с именем прибора. "
             "Одна операция для окна и агента."))
async def param_set(
    project: Project,
    name: Annotated[str, Arg(help="имя параметра")],
    value: Annotated[float, Arg(help="новое значение в единицах параметра")],
    source: Annotated[str, Arg(help="чем и как измерено; делает параметр измеренным")] = "",
) -> dict[str, Any]:
    """Правка реестра параметров.

    Правила — те же, что у самого реестра, только применённые к правке:
      * производный не правится: у него формула;
      * угаданный без источника остаётся угаданным и обязан лежать в своём
        диапазоне — иначе это уже не та догадка;
      * с источником параметр становится измеренным: диапазон и пометка
        «мешает печати» уходят, потому что неизвестного больше нет;
      * измеренный меняется только с новым источником — число без того, чем
        его получили, это снова догадка, выданная за замер.
    """
    from datum.doc.params import ParamError, ParamRegistry
    from datum.doc.params_edit import EditError, set_fields, write_atomically

    def run():
        root = Path(project).resolve()
        path = root / "params" / "base.yaml"
        if not path.is_file():
            return _err(f"в проекте нет params/base.yaml: {root}")
        text = path.read_text(encoding="utf-8")
        reg = ParamRegistry.loads(text)
        if name not in reg:
            catalog = _material_catalog_params(root)
            if name in catalog:
                return _measure_material(path, text, name, catalog[name], value, source)
            close = sorted(n for n in reg.params if n.lower().startswith(name[:2].lower()))
            return _err(f"параметра {name!r} нет" + (f"; похожие: {close}" if close else ""))
        p = reg[name]
        before = p.display
        fields: dict[str, Any] = {"value": float(value)}
        remove: tuple[str, ...] = ()
        if p.provenance == "derived":
            return _err(f"{name} — производный: {p.expr}. Меняйте то, из чего он считается")
        if source.strip():
            fields |= {"provenance": "measured", "source": source.strip()}
            remove = ("blocking", "range", "validate_with")
        elif p.provenance == "measured":
            return _err(f"{name} измерен ({p.source or 'источник не записан'}). Новое "
                        f"значение без источника — это снова догадка; укажите source")
        elif p.provenance == "assumed" and p.range:
            from datum.doc.units import from_canonical

            lo, hi = (from_canonical(v, p.unit) for v in p.range)
            if not lo - 1e-12 <= float(value) <= hi + 1e-12:
                return _err(f"{name}: {value:g} вне диапазона догадки [{lo:g}, {hi:g}] "
                            f"{p.unit}. Если это замер — укажите source")
        try:
            updated = set_fields(text, name, fields, remove=remove)
            check = ParamRegistry.loads(updated)[name]
        except (EditError, ParamError) as e:
            return _err(f"правка не легла: {e}")
        if abs(check.display - float(value)) > 1e-9 * max(1.0, abs(float(value))):
            return _err(f"правка не легла: после записи {name} читается как "
                        f"{check.display:g}, а не {value:g}")
        write_atomically(path, updated)
        became = (f", теперь {check.provenance}" if check.provenance != p.provenance else "")
        return {"name": name, "before": before, "after": check.display, "unit": p.unit,
                "provenance": check.provenance, "source": check.source,
                "report": f"{name}: {before:g} → {check.display:g} {p.unit}{became}"
                          + (f" ({check.source})" if source.strip() else "")}

    return _guard(run)


def _material_catalog_params(root: Path) -> dict:
    """Параметры MAT_* материала проекта из каталога (как их добавляет проект)."""
    import tomllib

    from datum.doc.materials import material_params

    manifest = root / "datum.toml"
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {}
    except tomllib.TOMLDecodeError:
        return {}
    return material_params(str(data.get("printer", {}).get("material", "PLA")))


def _measure_material(path: Path, text: str, name: str, body: dict, value: float,
                      source: str) -> dict:
    """Свой замер свойства пластика: строкой в params/base.yaml, поверх каталога.

    Без источника — отказ: каталожное число и так догадка из интернета, а
    число без того, чем его получили, — ещё одна.
    """
    from datum.doc.params import ParamError, ParamRegistry
    from datum.doc.params_edit import _render, write_atomically

    if not source.strip():
        return _err(f"{name} — из каталога материала (doc/materials.py). Свой замер "
                    f"записывается с источником: укажите source — чем и как измерено")
    unit = body.get("unit", "")
    line = (f"{name}: {{value: {_render(float(value))}, unit: {_render(unit, 'unit')}, "
            f"provenance: measured, source: {_render(source.strip())}}}")
    updated = (text if text.endswith("\n") or not text else text + "\n") + (
        "\n# Замер свойства пластика — перебивает каталог материала (doc/materials.py).\n"
        + line + "\n")
    try:
        check = ParamRegistry.loads(updated)[name]
    except ParamError as e:
        return _err(f"правка не легла: {e}")
    write_atomically(path, updated)
    before = body.get("value")
    return {"name": name, "before": before, "after": check.display, "unit": unit,
            "provenance": "measured", "source": check.source,
            "report": f"{name}: каталог {before:g} → замер {check.display:g} {unit} "
                      f"({check.source}); записано в params/base.yaml"}
