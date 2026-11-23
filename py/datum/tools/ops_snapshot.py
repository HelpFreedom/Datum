"""Версии: снимок, список, сравнение.

Часть операций Datum (`tools/ops.py` — их общий вход: оттуда строятся
MCP-инструменты и команды CLI). Соглашение то же: операция возвращает
словарь, `report` — текст для человека, об ошибке — ключ `error`.
"""

from __future__ import annotations

from typing import Annotated, Any

from datum.tools.ops_base import Project, _err, _guard, open_project
from datum.tools.registry import Arg, op


# ------------------------------------------------------------------ версии
@op("snapshot_create", cli_name="snapshot", group="версии",
    isolate=True,
    title="Снять версию", danger="mutates",
    summary=("Заморозить состояние проекта: параметры, метрики деталей и таблицу "
             "проверок. Снимок ложится в snapshots/ и служит опорой для сравнения."))
async def snapshot_create(
    project: Project,
    tag: Annotated[str, Arg(help="короткое имя версии", metavar="ТЕГ")],
    notes: Annotated[str, Arg(help="чем эта версия отличается")] = "",
    overrides: Annotated[dict[str, float] | None,
                         Arg(help="подстановки параметров в канонических единицах "
                                  "(мм, градусы, Н, МПа, г)")] = None,
) -> dict[str, Any]:
    from datum.doc.snapshot import Snapshot

    def run():
        pr = open_project(project)
        snap = Snapshot.create(pr, tag, notes=notes, overrides=overrides or None)
        path = snap.save(pr.root)
        return {"tag": snap.tag, "created": snap.created,
                "path": str(path.relative_to(pr.root)),
                "print_ready": snap.print_ready,
                "report": snap.report() + f"\n\nзаписано: {path.relative_to(pr.root)}"}

    return _guard(run)


@op("snapshot_list", cli_name="snapshots", group="версии",
    isolate=True,
    title="Список снимков",
    summary="Какие версии проекта уже сняты и в каком они состоянии.")
async def snapshot_list(project: Project) -> dict[str, Any]:
    from datum.doc.snapshot import Snapshot, list_snapshots

    def run():
        pr = open_project(project)
        out, lines = [], []
        for f in list_snapshots(pr.root):
            try:
                s = Snapshot.load(f)
            except Exception as e:
                # Один битый файл не должен прятать все остальные.
                out.append({"file": f.name, "error": f"{type(e).__name__}: {e}"})
                lines.append(f"  {f.name}\n      НЕ ЧИТАЕТСЯ: {type(e).__name__}: {e}")
                continue
            ok = "OK" if s.checks.get("ok") else f"{s.checks.get('n_fail', 0)} FAIL"
            out.append({"file": f.name, "tag": s.tag, "created": s.created,
                        "print_ready": s.print_ready,
                        "checks_ok": bool(s.checks.get("ok")),
                        "n_fail": s.checks.get("n_fail", 0)})
            lines.append(f"  {f.name}\n      «{s.tag}» — "
                         f"{'готов' if s.print_ready else 'не готов'} к печати, "
                         f"проверки {ok}")
        return {"snapshots": out,
                "report": "\n".join(lines) if lines else "снимков нет"}

    return _guard(run)


@op("snapshot_diff", cli_name="diff", group="версии",
    isolate=True,
    title="Что изменилось между версиями",
    summary=("Сравнить два снимка: изменения параметров, метрик деталей и переходы "
             "проверок (починилось, сломалось, всё ещё падает). Ссылки — часть имени "
             "файла или номер с конца: -1 последний, -2 предпоследний. С image — ещё и "
             "форма картинкой: серое — общее, зелёное — добавилось, красное — убралось "
             "(агенту картинка приходит в ответе; путь к PNG — там же)."),
    exit_code=lambda r: 1 if (r.get("error") or r.get("regression")) else 0)
async def snapshot_diff(
    project: Project,
    a: Annotated[str, Arg(help="снимок «до»")] = "-2",
    b: Annotated[str, Arg(help="снимок «после»")] = "-1",
    image: Annotated[bool, Arg(help="нарисовать, что изменилось в форме деталей")] = False,
) -> dict[str, Any]:
    from datum.doc.diff import diff as make_diff
    from datum.doc.snapshot import Snapshot, list_snapshots, pick_snapshot

    def run():
        pr = open_project(project)
        files = list_snapshots(pr.root)
        if len(files) < 2:
            return _err("в проекте меньше двух снимков — сравнивать нечего")

        try:
            path_a, path_b = pick_snapshot(files, a), pick_snapshot(files, b)
        except KeyError as e:
            return _err(str(e.args[0]))
        if path_a == path_b:
            return _err(f"«до» и «после» — один и тот же снимок {path_a.name}: "
                        f"сравнивать нечего")
        snap_a, snap_b = Snapshot.load(path_a), Snapshot.load(path_b)
        d = make_diff(snap_a, snap_b)
        out = d.as_dict()
        out["report"] = d.report()
        if image:
            from datum.doc.geomdiff import geometry_diff

            g = geometry_diff(pr.root, snap_a, snap_b)
            out["geometry"] = g.as_dict()
            lines = ["", "Форма:"] + g.lines()
            if g.png is not None:
                digest = pr.cache.put_blob(g.png, "png", kind="version-diff",
                                           meta={"a": path_a.name, "b": path_b.name})
                blob = pr.cache.blob_path(digest, "png")
                path = (str(blob.relative_to(pr.root))
                        if blob is not None and blob.is_relative_to(pr.root) else str(blob))
                out["image"] = {"url": f"/obj/{digest}.png", "path": path}
                lines.append(f"картинка: {path}")
            elif not g.missing:
                lines.append("  рисовать нечего: ни одна деталь не собралась")
            out["report"] += "\n".join(lines)
        return out

    return _guard(run)
