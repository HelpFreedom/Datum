"""Чужие модели и шрифты: диагностика, импорт, ремонт.

Часть операций Datum (`tools/ops.py` — их общий вход: оттуда строятся
MCP-инструменты и команды CLI). Соглашение то же: операция возвращает
словарь, `report` — текст для человека, об ошибке — ключ `error`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal

from datum.tools.ops_base import (
    Project,
    _err,
    _guard,
    clear_project_cache,
    open_project,
    project_root_only,
)
from datum.tools.registry import Arg, op


# ------------------------------------------------------------------ чужие меши
#: Запас памяти операций над чужим мешем, сверх того, что занял родитель.
#: Умолчанию воркера (1 ГБ) хватает скрипту детали, но не файлу: замерено, что
#: чтение, сборка сетки и диагноз модели в 1.48 млн треугольников занимают около
#: 1.6 ГБ адресного пространства — примерно 1.1 КБ на треугольник. 4 ГБ — это
#: около 3.5 млн: с запасом больше самой тяжёлой модели корпуса (1.27 млн), и
#: всё ещё предел, а не вся память машины.
FOREIGN_MESH_HEADROOM_MB = 4096


@op("mesh_diagnose", cli_name="diagnose", group="меши",
    isolate=True, memory_headroom_mb=FOREIGN_MESH_HEADROOM_MB,
    title="Диагностика меша",
    summary=("Померить файл меша: габариты, объём, замкнутость, тела, запаянные "
             "полости, дыры и неманифольдные рёбра. Делать это до любой работы "
             "с чужой моделью."))
async def mesh_diagnose(
    path: Annotated[str, Arg(help="файл STL/3MF/OBJ/PLY", metavar="ФАЙЛ")],
) -> dict[str, Any]:
    from datum.geom.repair import diagnose, load_mesh

    def run():
        d = diagnose(load_mesh(path))
        probs = d.problems()
        return {**d.as_dict(),
                "report": "\n".join([f"{Path(path).name}: {d.triangles:,} граней"]
                                    + [f"  — {p}" for p in probs or ["всё в порядке"]])}

    return _guard(run)


def _model_name(text: str) -> str:
    """Имя модели из имени файла или объекта: пробелы и точки — в дефис.

    Пробелы и точки в имени каталога — источник беды на ровном месте.
    """
    return re.sub(r"[^A-Za-zА-Яа-яЁё0-9_-]+", "-", text.strip()).strip("-")[:64]


def _store_model(root: Path, chosen: str, *, source: Path | None, mesh, meta_extra: dict,
                 repair_level: str, keep_largest: bool = True) -> dict:
    """Положить модель в `imported/<chosen>/` ТРАНЗАКЦИЕЙ: исходник, результат
    ремонта и журнал собираются рядом и подменяют прежнее, только когда готово
    целиком.

    Раньше прежние source.* и result.* удалялись ДО загрузки нового файла:
    битый повторный импорт оставлял каталог с meta.json от прежней модели и без
    результата, и опись показывала «цела» то, чего уже не было.

    source — файл, который кладётся как есть (`source.<ext>`); None — исходником
    служит `mesh` как прочитан, до ремонта (объект из файла многих объектов).
    """
    import json
    import shutil
    import tempfile
    from datetime import datetime, timezone

    from datum.build.imported import imported_dir
    from datum.geom.repair import diagnose, load_mesh, repair

    folder = imported_dir(root) / chosen
    folder.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{chosen}.", dir=folder.parent))
    try:
        if source is not None:
            kept = staging / f"source{source.suffix.lower()}"
            shutil.copy2(source, kept)
            mesh = load_mesh(kept)
        else:
            mesh.export(staging / "source.stl")
        before = diagnose(mesh)

        steps: list[dict] = []
        if repair_level == "none":
            result_mesh, after = mesh, before
        else:
            outcome = repair(mesh, level=repair_level, keep_largest=keep_largest)
            result_mesh, after = outcome.mesh, outcome.after
            steps = [{"op": st.op, "note": st.note, "params": st.params,
                      "ms": round(st.ms, 1)} for st in outcome.steps]

        # Результат — всегда STL: он читается всем и не несёт лишнего.
        result_mesh.export(staging / "result.stl")
        meta = {
            "name": chosen,
            **meta_extra,
            "imported": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "repair_level": repair_level,
            "steps": steps,
            "before": before.as_dict(),
            "after": after.as_dict(),
        }
        (staging / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        retired = None
        if folder.exists():
            retired = folder.with_name(f".{chosen}.прежний")
            shutil.rmtree(retired, ignore_errors=True)
            folder.rename(retired)
        staging.rename(folder)
        if retired is not None:
            shutil.rmtree(retired, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {"folder": folder, "steps": steps, "before": before, "after": after,
            "mesh": result_mesh}


@op("import_model", cli_name="import", group="чужие модели",
    isolate=True, danger="mutates", memory_headroom_mb=FOREIGN_MESH_HEADROOM_MB,
    title="Импортировать чужую модель в проект",
    summary=("Положить чужой меш в проект ОБРАЗЦОМ: исходник сохраняется нетронутым, "
             "рядом ложится журнал ремонта и готовый к работе результат. Деталью он не "
             "становится — так берут скан или покупную вещь под ложемент. Модель на "
             "стол деталями одним шагом — import_to_bed."))
async def import_model(
    project: Project,
    path: Annotated[str, Arg(help="файл STL/3MF/OBJ/PLY")],
    name: Annotated[str | None, Arg(help="имя в проекте; пусто — по имени файла")] = None,
    repair_level: Annotated[Literal["auto", "light", "remesh", "none"],
                            Arg(help="ремонт: auto, light, remesh или none")] = "auto",
) -> dict[str, Any]:
    """Исходник хранится отдельно и НЕ ТРОГАЕТСЯ.

    Ремонт — это решение, а не факт. Через месяц надо уметь ответить, что
    именно сделали с чужой моделью, и сравнить с оригиналом; а журнал в
    `meta.json` позволяет переиграть ремонт с нуля.
    """
    import hashlib

    from datum.build.imported import NAME_RE

    def run():
        source = Path(path).expanduser()
        if not source.is_file():
            return _err(f"нет такого файла: {source}")

        chosen = _model_name(name or source.stem)
        if not NAME_RE.match(chosen):
            return _err(f"из {source.name!r} не выходит годного имени; задайте --name")

        if repair_level not in ("auto", "light", "remesh", "none"):
            return _err(f"repair_level {repair_level!r}: допустимо auto, light, remesh "
                        f"или none")

        pr = open_project(project)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        stored = _store_model(pr.root, chosen, source=source, mesh=None,
                              meta_extra={"source": source.name, "digest": digest},
                              repair_level=repair_level)
        steps, before, after = stored["steps"], stored["before"], stored["after"]

        lines = [f"{chosen}: {source.name} → imported/{chosen}/"]
        for st in steps:
            lines.append(f"  {st['op']:16} {st['note']}")
        lines.append(f"  в скрипте детали: imported({chosen!r})")
        return {"name": chosen, "folder": str(stored["folder"]), "digest": digest,
                "steps": steps, "before": before.as_dict(), "after": after.as_dict(),
                "healthy": bool(after.healthy),
                "report": "\n".join(lines)}

    return _guard(run)


@op("import_to_bed", cli_name="import-bed", group="чужие модели",
    isolate=True, danger="mutates", memory_headroom_mb=FOREIGN_MESH_HEADROOM_MB,
    title="Импортировать модель на стол деталями",
    summary=("Одним шагом: чужой файл → детали на столе. Каждый объект файла (у 3MF — "
             "объект стола слайсера; части одного объекта — вместе) становится своей "
             "деталью: модель в imported/, скрипт в parts/, место на столе — как в "
             "файле или рядами, мимо уже стоящих деталей. Ремонт — как у import_model, "
             "но тела объекта не отбрасываются."))
async def import_to_bed(
    project: Project,
    path: Annotated[str, Arg(help="файл STL/3MF/OBJ/PLY", metavar="ФАЙЛ")],
    name: Annotated[str | None, Arg(help="имя модели; пусто — по имени файла (у "
                                         "объектов 3MF — по их именам)")] = None,
    repair_level: Annotated[Literal["auto", "light", "remesh", "none"],
                            Arg(help="ремонт: auto, light, remesh или none")] = "auto",
) -> dict[str, Any]:
    """Прежде модель на стол шла тремя шагами (диагноз, импорт, «В деталь»), а
    из 3MF приходила ОДНА деталь: чтение склеивало объекты в одну сетку, а
    ремонт оставлял крупнейшее тело. Здесь объекты читаются порознь
    (`load_objects`), тела не отбрасываются, и каждый объект встаёт на стол
    своей деталью (`doc/bedlayout.py`).

    Исходник: файл с одним объектом кладётся как есть; у файла многих объектов
    исходник каждой модели — её объект КАК ПРОЧИТАН, до ремонта (`source.stl`),
    а отпечаток всего файла и номер объекта — в meta.json. Целый файл в каждой
    модели повторял бы его столько раз, сколько в нём объектов.

    Всё или ничего: отказ посередине убирает уже созданные модели и детали.
    """
    import hashlib
    import shutil

    from datum.build.imported import NAME_RE, imported_dir, imported_names, part_file_stem, part_source
    from datum.doc.bedlayout import arrange
    from datum.geom.measure import bbox
    from datum.geom.repair import load_objects

    def run():
        source = Path(path).expanduser()
        if not source.is_file():
            return _err(f"нет такого файла: {source}")
        if repair_level not in ("auto", "light", "remesh", "none"):
            return _err(f"repair_level {repair_level!r}: допустимо auto, light, remesh "
                        f"или none")
        base = _model_name(name or source.stem)
        if not NAME_RE.match(base):
            return _err(f"из {source.name!r} не выходит годного имени; задайте name")

        # Проект — заново, не из кеша процесса: куда ставить, зависит от того,
        # что стоит на столе СЕЙЧАС, а прошлый импорт в этом же процессе
        # (CLI --in-process, тесты) кеш не обновил бы.
        clear_project_cache()
        pr = open_project(project)
        objects, file_notes = load_objects(source)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        single = len(objects) == 1

        # Имена: у одного объекта — по файлу; у многих — по именам объектов
        # (расширение, которое слайсер оставляет от файла, — долой), безымянный —
        # «файл-номер». Занятое (модель, деталь, файл детали) — с хвостом «-2».
        taken = set(imported_names(pr.root)) | set(pr.parts)
        names: list[str] = []
        for i, loaded in enumerate(objects):
            # Слайсер оставляет в имени объекта расширение файла, а копии метит
            # хвостом: «perie mare fusion.stl_2» → «perie mare fusion_2».
            label = re.sub(r"\.(stl|obj|3mf|ply|step|stp)(?=_\d+$|$)", "", loaded.name.strip(),
                           flags=re.I)
            wanted = base if single or name else _model_name(label)
            if not single and (name or not NAME_RE.match(wanted or "")):
                wanted = f"{base}-{i + 1}"
            chosen, k = wanted, 2
            while (chosen in taken
                   or (pr.root / "parts" / f"{part_file_stem(chosen)}.py").exists()
                   or (imported_dir(pr.root) / chosen).exists()):
                chosen, k = f"{wanted[:60]}-{k}", k + 1
            taken.add(chosen)
            names.append(chosen)

        # Где уже стоят детали — их габариты в плане (из кеша сборки). Не
        # собралась деталь — её места не знаем, и это пишется в отчёт.
        occupied, unknown = [], []
        if pr.parts:
            try:
                built = pr.build()
            except Exception as e:                 # проект не собирается целиком
                built, unknown = {}, [f"сборка: {e}"]
            for part_name, res in built.items():
                if res.ok:
                    (x0, y0, _), (x1, y1, _) = bbox(res.solid)
                    occupied.append((x0, y0, x1, y1))
                else:
                    unknown.append(part_name)

        made_models: list[Path] = []
        made_parts: list[Path] = []
        try:
            stored = []
            for i, (loaded, chosen) in enumerate(zip(objects, names)):
                extra = {"source": source.name, "digest": digest}
                if not single:
                    # Отпечаток модели — свой у каждого объекта: по нему кеш и
                    # окно отличают модели друг от друга.
                    extra = {"source": source.name,
                             "digest": hashlib.sha256(f"{digest}#{i}".encode()).hexdigest(),
                             "file_digest": digest,
                             "object": {"index": i, "name": loaded.name, "of": len(objects)}}
                if loaded.notes:
                    extra["read"] = list(loaded.notes)
                # Одиночный объект: исходник — файл как есть, а сетку из него
                # `_store_model` читает той же слайсерской дорогой (`load_mesh`).
                result = _store_model(pr.root, chosen, source=source if single else None,
                                      mesh=None if single else loaded.mesh, meta_extra=extra,
                                      repair_level=repair_level, keep_largest=False)
                made_models.append(result["folder"])
                stored.append(result)

            footprints = []
            for result in stored:
                lo, hi = result["mesh"].bounds
                footprints.append((float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1])))
            plan = arrange(footprints, (pr.bed[0], pr.bed[1]), occupied)

            parts_dir = pr.root / "parts"
            parts_dir.mkdir(parents=True, exist_ok=True)
            placed = []
            for chosen, loaded, result, (cx, cy) in zip(names, objects, stored, plan.centres):
                after = result["after"]
                offset = (cx - pr.bed[0] / 2, cy - pr.bed[1] / 2)
                target = parts_dir / f"{part_file_stem(chosen)}.py"
                # Текст — ДО создания файла: иначе отказ на нём оставил бы пустой
                # файл детали, о котором откат не знает.
                text = part_source(chosen, chosen, at=offset, bodies=int(after.bodies),
                                   voids=int(after.sealed_voids) > 0)
                # Эксклюзивное создание: между проверкой и записью файл мог появиться.
                with open(target, "x", encoding="utf-8") as f:
                    made_parts.append(target)
                    f.write(text)
                notes = list(loaded.notes)
                # Перестройка по вокселям меняет форму — об этом громко, а не
                # одним словом в списке шагов: человек сравнивает с оригиналом.
                if any(st["op"] == "перестройка" for st in result["steps"]):
                    notes.append("ВНИМАНИЕ: ФОРМА УПРОЩЕНА перестройкой по вокселям — "
                                 "сетка из файла не замыкалась; мелкие детали могли "
                                 "пропасть, сверьте с оригиналом")
                placed.append({"part": chosen, "model": chosen,
                               "notes": notes,
                               "file": target.relative_to(pr.root).as_posix(),
                               "centre": [round(cx, 2), round(cy, 2)],
                               "bodies": int(after.bodies),
                               "sealed_voids": int(after.sealed_voids),
                               "healthy": bool(after.healthy),
                               "steps": [st["op"] for st in result["steps"]]})
        except Exception:
            for f in made_parts:
                f.unlink(missing_ok=True)
            for folder in made_models:
                shutil.rmtree(folder, ignore_errors=True)
            raise
        finally:
            clear_project_cache()            # в проекте новые детали

        lines = [f"{source.name}: объектов {len(objects)} → деталей {len(placed)} на столе"
                 + (f" ({plan.note})" if plan.note else "")]
        for item in placed:
            notes = []
            if item["steps"]:
                notes.append("ремонт: " + "+".join(item["steps"]))
            if item["bodies"] > 1:
                notes.append(f"тел {item['bodies']}")
            if item["sealed_voids"]:
                notes.append(f"ЗАПАЯННЫХ ПОЛОСТЕЙ {item['sealed_voids']}")
            if not item["healthy"]:
                notes.append("с замечаниями")
            notes += item["notes"]
            lines.append(f"  {item['part']:24} {item['file']:32} центр "
                         f"{item['centre'][0]:.0f}, {item['centre'][1]:.0f}"
                         + (f"  · {', '.join(notes)}" if notes else ""))
        if unknown:
            lines.append("  место не сверено с несобранными: " + ", ".join(unknown))
        lines += [f"  {n}" for n in file_notes]
        if not plan.fits:
            lines.insert(0, "ВНИМАНИЕ: " + plan.note)
        return {"parts": placed, "fits": plan.fits, "file_notes": file_notes,
                "kept_file_layout": plan.kept_file_layout, "note": plan.note,
                "report": "\n".join(lines)}

    return _guard(run)


@op("imported_list", cli_name="imports", group="чужие модели",
    isolate=True,
    title="Что импортировано в проект",
    summary="Список чужих моделей проекта: откуда взяты, что с ними сделали и целы ли.")
async def imported_list(project: Project) -> dict[str, Any]:
    import json

    from datum.build.imported import imported_dir, imported_names

    def run():
        pr = open_project(project)
        out = []
        for model in imported_names(pr.root):
            meta_path = imported_dir(pr.root) / model / "meta.json"
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):           # pragma: no cover
                continue
            after = meta.get("after", {})
            out.append({
                "name": model,
                "source": meta.get("source", ""),
                "imported": meta.get("imported", ""),
                "steps": [st["op"] for st in meta.get("steps", [])],
                "triangles": after.get("triangles", 0),
                "watertight": after.get("watertight", False),
                "healthy": after.get("healthy", False),
            })
        lines = [f"  {m['name']:20} {m['source'][:28]:30} {m['triangles']:>8} гр. "
                 + ("цел" if m["healthy"] else "с замечаниями")
                 + (f"  ремонт: {'+'.join(m['steps'])}" if m["steps"] else "")
                 for m in out]
        return {"models": out,
                "report": "\n".join(lines) or "  в проекте нет импортированных моделей"}

    return _guard(run)


@op("imported_views", cli_name="import-views", group="чужие модели",
    isolate=True, memory_headroom_mb=FOREIGN_MESH_HEADROOM_MB,
    title="Импортированные модели как образцы на столе",
    summary=("Вид каждой импортированной модели для окна — адрес GLB по отпечатку, "
             "габарит — и какие файлы проекта её уже берут (imported(\"имя\")). "
             "Образец — только вид: в проверках и выгрузке он не участвует."))
async def imported_views(project: Project) -> dict[str, Any]:
    import json

    from datum.build.imported import imported_dir, imported_names, result_path, used_by
    from datum.geom.repair import load_mesh
    from datum.io.glb import mesh_to_glb

    def run():
        pr = open_project(project)
        # Памятка «отпечаток модели → опубликованный вид»: модель в полтора
        # миллиона треугольников перегонять в GLB на каждое обновление окна
        # незачем, а отпечаток из meta.json меняется с каждым импортом.
        memo_path = pr.root / ".datum" / "imported-views.json"
        try:
            memo = json.loads(memo_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            memo = {}
        out, fresh = [], {}
        for model in imported_names(pr.root):
            meta = json.loads((imported_dir(pr.root) / model / "meta.json")
                              .read_text(encoding="utf-8"))
            key = meta.get("digest", "")
            seen = memo.get(model)
            if seen and seen.get("key") == key and pr.cache.blob_path(seen["digest"], "glb"):
                view = {k: v for k, v in seen.items() if k != "key"}
            else:
                path = result_path(pr.root, model)
                if path is None:
                    continue
                mesh = load_mesh(path)
                blob = mesh_to_glb(mesh.vertices, mesh.faces, name=model)
                digest = pr.cache.put_blob(blob, "glb", kind="imported",
                                           meta={"imported": model})
                lo, hi = mesh.bounds
                view = {"digest": digest, "url": f"/obj/{digest}.glb", "bytes": len(blob),
                        "tris": int(len(mesh.faces)),
                        "bbox": [round(float(v), 4) for v in (*lo, *hi)]}
            fresh[model] = {**view, "key": key}
            out.append({"name": model, **view, "used_by": used_by(pr.root, model)})
        if fresh != memo:
            memo_path.parent.mkdir(parents=True, exist_ok=True)
            from datum.doc.params_edit import write_atomically

            write_atomically(memo_path, json.dumps(fresh, ensure_ascii=False))
        lines = [f"  {m['name']:24} {m['tris']:>9,} гр."
                 + (f"  берут: {', '.join(m['used_by'])}" if m["used_by"] else "  не используется")
                 for m in out]
        return {"models": out,
                "report": "\n".join(lines) or "  в проекте нет импортированных моделей"}

    return _guard(run)


@op("imported_to_part", cli_name="import-part", group="чужие модели",
    isolate=True, danger="mutates",
    title="Сделать деталь из импортированной модели",
    summary=("Создать parts/<имя>.py, который берёт модель imported(\"имя\") и ставит её "
             "в центр стола. Дальше это обычная деталь: замер, срез, проверки, выгрузка, "
             "а скрипт можно дописывать. Существующее не перезаписывается."))
async def import_to_part(
    project: Project,
    name: Annotated[str, Arg(help="имя импортированной модели", metavar="МОДЕЛЬ")],
    part: Annotated[str | None, Arg(help="имя детали; пусто — как у модели")] = None,
) -> dict[str, Any]:
    from datum.build.imported import imported_names, part_file_stem, part_source

    def run():
        pr = open_project(project)
        if name not in imported_names(pr.root):
            have = imported_names(pr.root)
            return _err(f"модель {name!r} не импортирована"
                        + (f"; есть: {', '.join(have)}" if have else ""))
        title = (part or name).strip()
        if not title:
            return _err("пустое имя детали")
        if title in pr.parts:
            return _err(f"деталь {title!r} уже есть в проекте — задайте другое имя (part)")
        stem = part_file_stem(title)
        target = pr.root / "parts" / f"{stem}.py"
        if target.exists():
            return _err(f"{target.relative_to(pr.root)} уже есть — чужое не перезаписывается; "
                        f"задайте другое имя (part)")
        target.parent.mkdir(parents=True, exist_ok=True)
        # Эксклюзивное создание: между проверкой и записью файл мог появиться.
        with open(target, "x", encoding="utf-8") as f:
            f.write(part_source(name, title))
        rel = target.relative_to(pr.root).as_posix()
        return {"part": title, "file": rel,
                "report": f"деталь {title!r}: {rel} — модель {name!r} в центре стола"}

    return _guard(run)


#: Замыкание дыр при перестройке — не больше стольких вокселей. Каждый шаг
#: расширения скругляет углы на воксель; при 20 это уже не ремонт, а другая
#: форма. Умолчание — 2.
REPAIR_CLOSE_MAX = 20


@op("mesh_repair", cli_name="repair", group="меши",
    isolate=True, memory_headroom_mb=FOREIGN_MESH_HEADROOM_MB,
    title="Починить меш", danger="mutates",
    summary=("Починить чужой меш лестницей: чистка, ориентация, заделка дыр, "
             "отбрасывание мусора, при нужде воксельная перестройка. Ведёт журнал "
             "сделанного. Без out ничего не записывает — только отчёт."),
    exit_code=lambda r: 1 if (r.get("error") or not r.get("ok", False)) else 0)
async def mesh_repair(
    path: Annotated[str, Arg(help="файл STL/3MF/OBJ/PLY", metavar="ФАЙЛ")],
    out: Annotated[str, Arg(help="куда записать результат")] = "",
    level: Annotated[Literal["auto", "light", "remesh"], Arg(help="auto, light или remesh")] = "auto",
    pitch: Annotated[float, Arg(help="размер вокселя при перестройке, мм; 0 — авто")] = 0.0,
    close: Annotated[int, Arg(help="замыкание дыр, вокселей")] = 2,
) -> dict[str, Any]:
    from datum.geom.repair import load_mesh, repair

    if level not in ("auto", "light", "remesh"):
        return _err(f"level должен быть auto, light или remesh, получено {level!r}")
    if pitch < 0:
        return _err(f"pitch {pitch:g} мм — шаг вокселя не бывает отрицательным; "
                    f"0 — подобрать самому")
    if not 0 <= close <= REPAIR_CLOSE_MAX:
        return _err(f"close {close} — замыкание дыр берётся от 0 до {REPAIR_CLOSE_MAX} "
                    f"вокселей: шире дыру так не закрыть, а форма оплывёт")

    def run():
        res = repair(load_mesh(path), level=level,
                     pitch=pitch or None, close_k=close)
        if out:
            res.mesh.export(out)
        d = res.as_dict()
        d["report"] = res.report() + (f"\n\nзаписано: {out}" if out else "")
        if out:
            d["written"] = out
        return d

    return _guard(run)


# ------------------------------------------------------------------ шрифты
@op("fonts_list", cli_name="fonts", group="проект",
    title="Шрифты для надписей",
    summary=("Какими шрифтами можно делать надписи на деталях (text_poly, text_z): "
             "шрифты проекта (fonts/), встроенные и системные. Системный шрифт "
             "становится доступен скрипту детали после font_add."))
async def fonts_list(
    project: Project,
    query: Annotated[str, Arg(help="часть имени для поиска среди системных; пусто — все")] = "",
) -> dict[str, Any]:
    from datum.geom.text import font_names, system_fonts

    def run():
        root = project_root_only(project)
        own = font_names(root)
        wanted = query.strip().lower()
        system = [n for n in system_fonts() if wanted in n.lower()]
        lines = [f"Проект (fonts/): {', '.join(own.get('проект', [])) or '—'}",
                 f"Встроенные: {', '.join(own.get('встроенные', []))}",
                 f"Системные{f' по «{query}»' if wanted else ''} ({len(system)}): "
                 + (", ".join(system[:80]) + (" …" if len(system) > 80 else "") if system else "—"),
                 "Системный — в проект: font_add(name=…), затем text_z(…, font=имя)."]
        return {"project_fonts": own.get("проект", []), "bundled": own.get("встроенные", []),
                "system": system, "report": "\n".join(lines)}

    return _guard(run)


@op("font_add", cli_name="font-add", group="проект", danger="mutates",
    title="Добавить шрифт в проект",
    summary=("Скопировать системный шрифт в fonts/ проекта, чтобы надписи деталей могли "
             "его брать. Проект остаётся самодостаточным: архив .datum несёт шрифт с "
             "собой, сборка у другого человека та же. Существующее не перезаписывается."))
async def font_add(
    project: Project,
    name: Annotated[str, Arg(help="имя шрифта, как его показывает fonts_list", metavar="ШРИФТ")],
) -> dict[str, Any]:
    import difflib
    import shutil

    from datum.geom.text import font_names, font_title, system_fonts

    def run():
        root = project_root_only(project)
        if name.strip().lower() in {n.lower() for n in font_names(root).get("проект", [])}:
            return {"font": name, "report": f"шрифт {name!r} уже в проекте"}
        fonts = system_fonts()
        by_lower = {k.lower(): (k, v) for k, v in fonts.items()}
        hit = by_lower.get(name.strip().lower())
        if hit is None:
            close = difflib.get_close_matches(name, list(fonts), n=5, cutoff=0.4)
            return _err(f"системного шрифта {name!r} нет"
                        + (f"; похожие: {', '.join(close)}" if close else
                           " — список: fonts_list"))
        title, path = hit
        folder = root / "fonts"
        folder.mkdir(exist_ok=True)
        target = folder / path.name
        if target.exists():
            if target.read_bytes() == path.read_bytes():
                return {"font": font_title(target), "report": f"шрифт {title!r} уже в проекте"}
            return _err(f"{target.relative_to(root)} уже есть и это другой файл — "
                        f"чужое не перезаписывается")
        shutil.copy2(path, target)
        used = font_title(target)
        return {"font": used, "file": target.relative_to(root).as_posix(),
                "report": (f"шрифт {used!r} → {target.relative_to(root).as_posix()}; "
                           f"в скрипте детали: text_z(…, font={used!r})")}

    return _guard(run)
