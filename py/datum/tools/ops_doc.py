"""Проект и его детали как файлы: сведения, положение, удаление и возврат, архив.

Часть операций Datum (`tools/ops.py` — их общий вход: оттуда строятся
MCP-инструменты и команды CLI). Соглашение то же: операция возвращает
словарь, `report` — текст для человека, об ошибке — ключ `error`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from datum.tools.ops_base import Project, _err, _guard, open_project, project_root_only
from datum.tools.registry import Arg, op


# ------------------------------------------------------------------ проект
@op("project_info", cli_name="info", group="проект",
    isolate=True,
    title="Сведения о проекте",
    summary=("Состав проекта: детали, число проверок, сводка по параметрам, профиль "
             "принтера. С этого стоит начинать работу с незнакомым проектом."))
async def project_info(project: Project) -> dict[str, Any]:
    def run():
        pr = open_project(project)
        s = pr.summary()
        par = s["params"]
        lines = [f"Проект: {s['name']}  ({s['root']})",
                 f"  детали:   {', '.join(s['parts']) or '—'}"]
        if s.get("not_printed"):
            lines.append(f"  не печатаются (printed=False — в проверках, не в выгрузке): "
                         f"{', '.join(s['not_printed'])}")
        lines += [f"  проверок: {s['checks']}"
                 + (f" (из них базовых печатности {s['baseline_checks']}: стенка, полости, "
                    f"стол, нависания у каждой печатаемой детали; своя того же вида в checks.yaml "
                    f"перебивает базовую)" if s.get("baseline_checks") else ""),
                 f"  страж:    {s['guard']}"]
        pp = s["printer"]
        lines.append(f"  принтер:  {pp.get('profile','—')}, стол {pp.get('bed')}, "
                     f"сопло {pp.get('nozzle')}, материал {pp.get('material')}")
        asm = s.get("assembly") or {}
        if asm.get("order"):
            шаги = []
            for i, n in enumerate(asm["order"]):
                st = asm["steps"].get(n, {})
                куда = ("" if i == 0 else
                        f" ({st.get('method')}{' ' + ','.join(st['insert']) if st.get('insert') else ''})")
                шаги.append(f"{n}{куда}")
            lines.append(f"  сборка:   {' → '.join(шаги)}"
                         + (f", выведено проверок {asm['derived_checks']}"
                            if asm.get("derived_checks") else ""))
            if asm.get("joints"):
                ходы = [f"{n} ({'поворот' if j['kind'] == 'revolute' else 'сдвиг'} "
                        f"{j['range'][0]:g}…{j['range'][1]:g}"
                        f"{'°' if j['kind'] == 'revolute' else ' мм'})"
                        for n, j in asm["joints"].items()]
                lines.append(f"  шарниры:  {', '.join(ходы)} — ход проверяется сам")
            if asm.get("unlisted"):
                # Деталь вне порядка на собираемость не проверяется. Об этом
                # надо сказать: иначе «зелено» означает «не смотрели».
                lines.append(f"  ВНЕ ПОРЯДКА СБОРКИ (не проверены на собираемость): "
                             f"{', '.join(asm['unlisted'])}")
        lines.append(f"  параметры: {par['total']} "
                     + ", ".join(f"{k}: {v}" for k, v in sorted(par["by_provenance"].items())))
        if not par["print_ready"]:
            lines.append(f"  НЕ ГОТОВ К ПЕЧАТИ: не измерено "
                         f"{', '.join(par['blocking_unknowns'])}")
        from datum.doc.archive import state as archive_state

        saved = archive_state(pr.root)
        if saved:
            lines.append(f"  архив:    {saved['path']}"
                         + (" — есть несохранённое" if saved["dirty"] else " — сохранено"))
        from datum.doc.archive import fingerprint as files_stamp

        # Отпечаток файлов проекта (stat, без чтения): по нему окно отличает
        # «файлы изменились» от отклика на свою же правку и не гоняет проверки
        # дважды (найдено обзором: каждая правка из окна — два прогона).
        return {**s, "archive": saved, "stamp": files_stamp(pr.root),
                "report": "\n".join(lines)}

    return _guard(run)


@op("part_place", cli_name="place", group="сборка",
    isolate=True, danger="mutates",
    title="Поставить деталь на столе",
    summary=("Записать положение детали в placement.yaml: сдвиг (мм) и углы (градусы, "
             "X→Y→Z, вокруг центра габарита детали). Скрипт детали не трогается; "
             "деталь пересобирается на новом месте, и проверки видят её там же."))
async def part_place(
    project: Project,
    part: Annotated[str, Arg(help="деталь", metavar="ДЕТАЛЬ")],
    move: Annotated[list[float] | None, Arg(help="сдвиг x,y,z, мм", metavar="X,Y,Z")] = None,
    rotate: Annotated[list[float] | None, Arg(help="углы x,y,z, градусы", metavar="X,Y,Z")] = None,
) -> dict[str, Any]:
    from datum.doc import placement as P

    def run():
        pr = open_project(project)
        if part not in pr.parts:
            return _err(f"нет детали {part!r}; есть: {', '.join(sorted(pr.parts))}")
        try:
            moved = P._triple(move, "move") if move is not None else None
            turned = P._triple(rotate, "rotate") if rotate is not None else None
        except P.PlacementError as e:
            return _err(str(e))
        placed: dict = {}

        def change(current: dict) -> dict:
            # Под блокировкой — от СВЕЖЕГО файла, а не от кеша проекта: иначе
            # соседняя одновременная правка терялась.
            now = current.get(part) or P.Placement()
            placed["new"] = P.Placement(move=moved if moved is not None else now.move,
                                        rotate=turned if turned is not None else now.rotate)
            return {**current, part: placed["new"]}

        try:
            P.update(pr.root, change)
        except P.PlacementError as e:
            return _err(str(e))
        new = placed["new"]
        return {"part": part, "placement": new.as_dict(),
                "report": (f"{part}: сдвиг {list(new.move)} мм, углы {list(new.rotate)}°"
                           if not new.identity else f"{part}: на месте, как её вернул скрипт")}

    return _guard(run)


def _trash_dir(root: Path) -> Path:
    return root / ".datum" / "trash"


@op("part_delete", cli_name="delete", group="сборка",
    isolate=True, danger="mutates",
    title="Удалить деталь из проекта",
    summary=("Удалить деталь из проекта целиком: файл parts/*.py, модель из imported/, "
             "которую брала только она, и проверки checks.yaml, ссылающиеся на неё. "
             "Всё уходит в корзину проекта .datum/trash/ — вернуть можно операцией "
             "part_restore. dry_run — только сказать, что будет. Деталь из порядка "
             "сборки не удаляется: без неё проект перестал бы открываться."))
async def part_delete(
    project: Project,
    part: Annotated[str, Arg(help="деталь", metavar="ДЕТАЛЬ")],
    dry_run: Annotated[bool, Arg(help="только рассказать, что будет удалено")] = False,
) -> dict[str, Any]:
    import datetime as dt
    import json
    import shutil

    from datum.build.imported import imported_dir, imported_names, used_by
    from datum.doc import checks_edit
    from datum.doc import placement as P
    from datum.doc.params_edit import write_atomically

    def mentions(value, names: set[str]) -> bool:
        """Стоит ли где-то в аргументах проверки "@деталь" из `names`."""
        if isinstance(value, str):
            return value.startswith("@") and value[1:] in names
        if isinstance(value, dict):
            return any(mentions(v, names) for v in value.values())
        if isinstance(value, (list, tuple)):
            return any(mentions(v, names) for v in value)
        return False

    def run():
        pr = open_project(project)
        if part not in pr.parts:
            return _err(f"нет детали {part!r}; есть: {', '.join(sorted(pr.parts))}")
        source = pr.part_files.get(part)
        if source is None:                              # pragma: no cover
            return _err(f"не найден файл, в котором объявлена деталь {part!r}")
        together = sorted(n for n, f in pr.part_files.items() if f == source)
        rel = source.relative_to(pr.root).as_posix()
        blocked = [n for n in together if n in pr.assembly.order]
        # Модель уходит вместе с деталью, только если её не берёт больше никто:
        # иначе у соседа пропала бы геометрия. А оставленная модель, которой
        # никто не пользуется, снова встала бы на стол образцом — «повтором»
        # только что удалённой детали.
        models = [m for m in imported_names(pr.root) if used_by(pr.root, m) == [rel]]
        names = set(together)
        drop = {k for k, d in enumerate(pr.check_decls)
                if isinstance(d, dict) and mentions(d.get("args", {}), names)}
        checks = sorted(str(pr.check_decls[k].get("id") or pr.check_decls[k].get("use"))
                        for k in drop)
        checks_yaml = pr.root / "checks" / "checks.yaml"
        new_checks, removed_checks, kept_reason = None, [], None
        if drop:
            try:
                new_checks, removed_checks = checks_edit.remove_entries(
                    checks_yaml.read_text(encoding="utf-8"), drop)
            except checks_edit.ChecksEditError as e:
                kept_reason = str(e)
        plan = {"part": part, "file": rel, "parts": together, "models": models,
                "checks": checks, "checks_kept": bool(kept_reason),
                "blocked": bool(blocked)}
        if blocked:
            reason = (f"{', '.join(blocked)} — в порядке сборки (assembly.yaml): без "
                      f"{'неё' if len(blocked) == 1 else 'них'} проект перестал бы "
                      f"открываться. Сначала уберите из порядка сборки")
            return {**plan, **_err(reason), "reason": reason}

        lines = [f"файл: {rel}"
                 + (f" (в нём ещё: {', '.join(n for n in together if n != part)})"
                    if len(together) > 1 else "")]
        if models:
            lines.append(f"модель: {', '.join(f'imported/{m}' for m in models)}")
        if checks and not kept_reason:
            lines.append(f"проверки: {', '.join(checks)}")
        if kept_reason:
            lines.append(f"проверки {', '.join(checks)} остались в checks.yaml и станут "
                         f"ошибкой: {kept_reason}")
        if dry_run:
            return {**plan, "report": "будет удалено —\n" + "\n".join(lines)}

        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        folder = _trash_dir(pr.root) / f"{stamp}-{source.stem}"
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        removed = {n: pr.placements[n].as_dict() for n in together if n in pr.placements}
        (folder / "meta.json").write_text(json.dumps(
            {"file": rel, "parts": together, "placement": removed, "models": models,
             "checks": removed_checks,
             "deleted": dt.datetime.now().astimezone().isoformat(timespec="seconds")},
            ensure_ascii=False, indent=2), encoding="utf-8")
        shutil.move(str(source), str(folder / rel))
        for m in models:
            (folder / "imported").mkdir(exist_ok=True)
            shutil.move(str(imported_dir(pr.root) / m), str(folder / "imported" / m))
        if new_checks is not None:
            # Вырезается ЗАНОВО, из файла, прочитанного под блокировкой: между
            # расчётом плана и этой строкой его могла править другая операция.
            def cut(text: str):
                import yaml

                decls = yaml.safe_load(text) or []
                now = {k for k, d in enumerate(decls if isinstance(decls, list) else [])
                       if isinstance(d, dict) and mentions(d.get("args", {}), names)}
                if not now:
                    return None
                fresh, pieces = checks_edit.remove_entries(text, now)
                removed_checks[:] = pieces
                return fresh

            before = list(removed_checks)
            try:
                checks_edit.update(pr.root, cut)
            except checks_edit.ChecksEditError as e:
                removed_checks[:] = []
                lines.append(f"проверки остались в checks.yaml и станут ошибкой: {e}")
            if removed_checks != before:
                meta_path = folder / "meta.json"
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                meta["checks"] = removed_checks
                write_atomically(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))
        if removed:
            P.update(pr.root, lambda current: {n: p for n, p in current.items()
                                                if n not in removed})
        return {**plan, "trash": folder.name,
                "report": "удалено —\n" + "\n".join(lines) + "\nвернуть: part_restore"}

    return _guard(run)


@op("trash_list", cli_name="trash", group="сборка",
    title="Корзина проекта",
    summary=("Что лежит в корзине проекта (.datum/trash/): удалённые детали с их "
             "моделями и проверками, которые можно вернуть операцией part_restore."))
async def trash_list(project: Project) -> dict[str, Any]:
    """Список корзины. Проект не открывается: операция идёт в процессе сервера."""
    import json

    def run():
        root = project_root_only(project)
        base = _trash_dir(root)
        entries = []
        for folder in sorted(base.iterdir()) if base.is_dir() else []:
            try:
                meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue                          # не запись корзины — не показываем
            if not isinstance(meta, dict):
                continue
            parts = [str(p) for p in meta.get("parts") or [] if isinstance(p, str)]
            models = [str(m) for m in meta.get("models") or [] if isinstance(m, str)]
            entries.append({"id": folder.name, "parts": parts,
                            "file": str(meta.get("file", "")),
                            "deleted": str(meta.get("deleted", "")), "models": models})
        lines = [f"  {e['id']}: {', '.join(e['parts']) or e['file']}"
                 + (f" (модель {', '.join(e['models'])})" if e["models"] else "")
                 + (f", удалено {e['deleted']}" if e["deleted"] else "")
                 for e in entries]
        return {"entries": entries,
                "report": (f"в корзине записей: {len(entries)}\n" + "\n".join(lines)
                           + "\nвернуть — part_restore(trash=<id>)") if entries
                else "корзина проекта пуста"}

    return _guard(run)


@op("part_restore", cli_name="restore", group="сборка",
    danger="mutates",
    title="Вернуть деталь из корзины проекта",
    summary=("Вернуть последнюю удалённую деталь (или названную trash) из .datum/trash/ "
             "на место — вместе с её положением, моделью и проверками. Существующее "
             "не перезаписывается."))
async def part_restore(
    project: Project,
    trash: Annotated[str | None, Arg(help="что вернуть; пусто — последнее удалённое")] = None,
) -> dict[str, Any]:
    import json
    import shutil

    from datum.build.imported import imported_dir
    from datum.doc import checks_edit
    from datum.doc import placement as P
    from datum.doc.params_edit import write_atomically

    def run():
        root = Path(project).resolve()
        base = _trash_dir(root)
        entries = sorted(d.name for d in base.iterdir() if (d / "meta.json").is_file()) \
            if base.is_dir() else []
        if not entries:
            return _err("корзина проекта пуста")
        name = trash or entries[-1]
        if name not in entries:
            return _err(f"в корзине нет {name!r}; есть: {', '.join(entries)}")
        folder = base / name
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        # Записи корзины не верят на слово: это файл в проекте, и чужой проект
        # мог положить туда `"file": "../../что-угодно"` — «Вернуть» писало
        # файл за пределами проекта (аудит 2026-11-21, воспроизведено).
        # Деталь возвращается только в parts/, модель — только по имени.
        from datum.build.imported import NAME_RE

        rel = meta.get("file")
        parts_dir = (root / "parts").resolve()
        if (not isinstance(rel, str) or not rel.endswith(".py")
                or Path(rel).is_absolute() or ".." in Path(rel).parts
                or (root / rel).resolve().parent != parts_dir
                or not (folder / rel).is_file()):
            return _err(f"запись корзины {name!r} испорчена: файл детали {rel!r} — не "
                        f"parts/<имя>.py; возврат отменён")
        named = meta.get("models") or []
        if not isinstance(named, list) or any(
                not isinstance(m, str) or not NAME_RE.match(m) for m in named):
            return _err(f"запись корзины {name!r} испорчена: имена моделей {named!r}; "
                        f"возврат отменён")
        target = root / rel
        models = [m for m in named if (folder / "imported" / m).is_dir()]
        # Всё проверяется ДО первого переноса: вернуть наполовину хуже, чем не
        # вернуть вовсе.
        taken = [rel] if target.exists() else []
        taken += [f"imported/{m}" for m in models if (imported_dir(root) / m).exists()]
        if taken:
            return _err(f"{', '.join(taken)} уже есть — чужое не перезаписывается; уберите "
                        f"или переименуйте, чтобы вернуть удалённое")
        pieces = meta.get("checks") or []
        checks_yaml = root / "checks" / "checks.yaml"

        def put_back(text: str):
            have = checks_edit.ids_of(text)
            fresh = [p for p in pieces if not checks_edit.ids_of(p) & have]
            return checks_edit.append_entries(text, fresh) if fresh else None

        if pieces:
            # Проба ДО первого переноса (вернуть наполовину хуже, чем не вернуть);
            # сама запись — ниже, под блокировкой и по свежему тексту.
            try:
                put_back(checks_yaml.read_text(encoding="utf-8")
                         if checks_yaml.is_file() else "")
            except checks_edit.ChecksEditError as e:
                return _err(str(e))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(folder / rel), str(target))
        for m in models:
            imported_dir(root).mkdir(exist_ok=True)
            shutil.move(str(folder / "imported" / m), str(imported_dir(root) / m))
        checks_note, checks_back = "", False
        if pieces:
            try:
                checks_back = checks_edit.update(root, put_back) is not None
            except checks_edit.ChecksEditError as e:
                checks_note = f"\nпроверки вернуть не вышло — допишите в checks.yaml сами: {e}"
        restored = meta.get("placement") or {}
        if restored:
            back = P.parse(restored)
            try:
                P.update(root, lambda current: {**current, **back})
            except P.PlacementError:
                P.save(root, back)            # испорченный файл — возвращённое важнее
        shutil.rmtree(folder, ignore_errors=True)
        extra = [f"imported/{m}" for m in models] + (["проверки"] if checks_back else [])
        return {"file": rel, "parts": meta.get("parts", []), "models": models,
                "report": f"возвращено: {rel} ({', '.join(meta.get('parts', []))})"
                          + (f" и {', '.join(extra)}" if extra else "") + checks_note}

    # Кода проекта не исполняет: переносит файлы и правит YAML — изоляции не нужно.
    return _guard(run)


@op("project_save", cli_name="save", group="проект", danger="mutates",
    title="Сохранить проект в архив .datum",
    summary=("Упаковать проект в один файл .datum — исходники, параметры, проверки, "
             "импорт, версии; без кеша и output/. Без dest — в архив, из которого "
             "проект открыт или куда сохранялся в прошлый раз. Затирается только "
             "архив Datum, чужой файл — никогда."))
async def project_save(
    project: Project,
    dest: Annotated[str | None, Arg(help="куда: путь к файлу .datum; пусто — туда же, "
                                         "куда в прошлый раз", metavar="ФАЙЛ")] = None,
    overwrite: Annotated[bool, Arg(help="перезаписать существующий архив")] = False,
) -> dict[str, Any]:
    import asyncio

    from datum.doc import archive

    def run():
        root = Path(project).resolve()
        target, replace = dest, overwrite
        if target is None:
            known = archive.state(root)
            if not known:
                return {**_err("проект ещё не сохранялся в архив — задайте, куда (dest)"),
                        "need_dest": True}
            # Сохранение «туда же» — это и есть перезапись своего архива.
            target, replace = known["path"], True
        try:
            packed = archive.pack(root, target, overwrite=replace)
        except archive.ArchiveError as e:
            return _err(str(e))                 # отказ — ожидаемый исход, не сбой
        note = (f"; пропущены ссылки: {', '.join(packed.links)}" if packed.links else "")
        return {"path": str(packed.path), "files": packed.files, "bytes": packed.bytes,
                "links_skipped": list(packed.links),
                "report": f"сохранено: {packed.path} — {packed.files} файлов, "
                          f"{packed.bytes / 1024:.0f} КБ{note}"}

    # Упаковка сотни мегабайт — секунды; в цикле событий сервера это замирание окна.
    return await asyncio.to_thread(_guard, run)


@op("project_unpack", cli_name="unpack", group="проект", danger="mutates",
    title="Распаковать архив .datum",
    summary=("Распаковать архив проекта рядом с ним (или в into) и вернуть путь к "
             "проекту. Если рядом уже лежит распакованный этот же архив, он и "
             "возвращается: правки после распаковки не затираются и не дублируются."))
async def project_unpack(
    path: Annotated[str, Arg(help="архив .datum", metavar="ФАЙЛ")],
    into: Annotated[str | None, Arg(help="новый каталог проекта; пусто — рядом с архивом")] = None,
) -> dict[str, Any]:
    import asyncio

    from datum.doc import archive

    def run():
        try:
            out = archive.unpack(path, into)
        except archive.ArchiveError as e:
            return _err(str(e))
        how = "открыт уже распакованный" if out.reused else f"распаковано файлов: {out.files}"
        if out.skipped:
            how += (f"; служебного в архиве пропущено: {out.skipped} (.git, .datum и "
                    f"подобное — упаковка Datum этого не кладёт)")
        return {"project": str(out.project), "reused": out.reused, "files": out.files,
                "report": f"{out.project} — {how}"}

    return await asyncio.to_thread(_guard, run)
