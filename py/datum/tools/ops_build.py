"""Сборка, выгрузка и нарезка деталей.

Часть операций Datum (`tools/ops.py` — их общий вход: оттуда строятся
MCP-инструменты и команды CLI). Соглашение то же: операция возвращает
словарь, `report` — текст для человека, об ошибке — ключ `error`.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from datum.tools.ops_base import Project, _err, _guard, open_project
from datum.tools.registry import Arg, op


@op("part_build", cli_name="build", group="сборка",
    isolate=True,
    title="Собрать детали",
    summary=("Собрать деталь или все детали проекта и вернуть метрики: объём, габариты, "
             "число тел, запаянные полости. При отказе стража называется виновная "
             "операция."),
    exit_code=lambda r: 1 if (r.get("error") or not r.get("ok", False)) else 0)
async def part_build(
    project: Project,
    parts: Annotated[list[str] | None, Arg(help="имена деталей; пусто — все",
                                           metavar="ДЕТАЛЬ")] = None,
    draft: Annotated[bool, Arg(help="черновик: скрипт видит draft() и сам огрубляет "
                                    "(шаг поля); проверки и выгрузка черновика не "
                                    "берут")] = False,
) -> dict[str, Any]:
    def run():
        from datum.build.place import bed_warnings
        from datum.geom.measure import bbox
        from datum.io.artifacts import publish_glb

        from datum.build.worker import emit

        pr = open_project(project)
        views: dict[str, dict] = {}

        def shown(name: str, r, done: int, total: int) -> None:
            """Деталь готова — сразу в окно, не дожидаясь остальных: при открытии
            проекта семнадцать деталей из кеша ждали восемнадцатую, и сцена всё
            это время стояла пустой. То же событие придёт и с результатом; сервер
            второй раз его не рассылает."""
            if r.ok and r.solid is not None:
                # Вид детали кладётся в кеш ВСЕГДА, на всех поверхностях: и
                # окну, и агенту полезно иметь адрес, по которому деталь можно
                # посмотреть. Обычная деталь стоит доли миллисекунды; самая
                # тяжёлая сборка корпуса (1.27 млн треугольников) — 33 мс.
                views[name] = publish_glb(pr.cache, r.solid, name=name,
                                          color=pr.parts[name].color)
                emit(_geom_event(name, r, views[name]))
            emit({"t": "build.progress", "part": name, "done": done, "total": total,
                  "ok": bool(r.ok)})

        results = pr.build(parts or None, draft=draft, on_built=shown)
        lines, out, events = [], {}, []
        for name, r in results.items():
            lines.append(r.line())
            culprit = getattr(r.error, "culprit", None)
            if not r.ok and culprit is not None:
                lines.append(f"       виновная операция #{culprit.index}: "
                             f"{culprit.describe()}")
            d = r.as_dict()
            d["printed"] = pr.parts[name].printed
            if r.ok and r.solid is not None:
                view = views.get(name) or publish_glb(pr.cache, r.solid, name=name,
                                                      color=pr.parts[name].color)
                d["view"] = view
                # Окну — сдвиг, углы и ось поворота: перетаскивание считает
                # новое положение от них, а не от того, что видит на экране.
                d["placement"] = pr.placement_of(name)
                events.append(_geom_event(name, r, view))
            out[name] = d
        # Где детали на столе: за краем — всегда, у края — когда собраны все
        # (детали узла могут нарочно стоять в стороне от центра каждая).
        # Непечатаемые (покупное, показная сборка) на стол не ложатся — о краю
        # стола они не предупреждают и в «собраны все» не считаются.
        printed = {n: r for n, r in results.items() if pr.parts[n].printed}
        boxes = {n: bbox(r.solid) for n, r in printed.items() if r.ok and r.solid is not None}
        place = bed_warnings(boxes, pr.bed, whole=not parts and len(boxes) == len(printed))
        lines += [f"  ! {w}" for w in place]
        # Опыт печати: урок общей базы, признак которого (поза на столе)
        # выполняется на этой детали. Напоминание, а не проверка.
        reminded = _lesson_reminders(pr, printed)
        lines += [f"  ! {w}" for w in reminded]
        return {"ok": all(r.ok for r in results.values()),
                "parts": out, "events": events, "bed_warnings": place,
                "lessons": reminded, "report": "\n".join(lines)}

    return _guard(run)



ExportFormat = Literal["3mf", "stl", "both"]


@op("export", cli_name="export", group="сборка",
    isolate=True, danger="mutates",
    title="Выгрузить для слайсера",
    summary=("Записать детали в output/ — 3MF и/или STL — в позе печати, объявленной у "
             "детали (print_up). Записанное перечитывается и сверяется с деталью. "
             "Отчёт — рецепт печати по каждой детали и вердикт проверок; выгружается "
             "всегда, но если проект не готов к печати, это сказано первой строкой."),
    exit_code=lambda r: 1 if r.get("error") else 0)
async def export(
    project: Project,
    parts: Annotated[list[str] | None, Arg(help="имена деталей; пусто — все",
                                           metavar="ДЕТАЛЬ")] = None,
    format: Annotated[ExportFormat, Arg(help="3mf, stl или both")] = "both",
    checks: Annotated[bool, Arg(help="прогнать проверки ради вердикта в отчёте")] = True,
) -> dict[str, Any]:
    """Файл для слайсера — последнее звено, и выгружается он ВСЕГДА.

    Решение владельца: печать остаётся решением человека, а не инструмента.
    Но незамеренное и проваленное не молчат: они — первые строки отчёта и
    `Description` в 3MF, то есть лежат в самом файле рядом с тем, что
    напечатано.
    """
    def run():
        from datum.build.worker import stage
        from datum.checks.dfam import DEFAULT_STEP, bed_contact, bridge_span, support_volume
        from datum.geom.measure import extents
        from datum.io.export import on_bed, safe_name, write_3mf, write_stl

        pr = open_project(project)
        names, skipped = _printed_names(pr, parts)
        if not names:
            return _err("выгружать нечего: " + _skipped_line(skipped))
        built = pr.build(names)
        broken = [r.line() for r in built.values() if not r.ok]
        if broken:
            return _err("не собрались детали — выгружать нечего:\n" + "\n".join(broken))

        printer = pr.manifest.get("printer", {})
        layer = float(printer.get("layer", 0.2))
        warnings, verdict, description = _readiness(pr, checks)

        out_dir = pr.root / "output"
        lines = []
        if warnings:
            lines.append("ВНИМАНИЕ: проект не готов к печати — выгружено всё равно:")
            lines += [f"  • {w}" for w in warnings]
            lines.append("")
        else:
            # Прошедшие проверки — тоже весть: прежде текст о них молчал, и по
            # отчёту не понять, прогонялись ли они (найдено обзором).
            lines.append(f"ГОТОВ К ПЕЧАТИ. Проверки: {verdict}")
            lines.append("")
        if skipped:
            lines.append(_skipped_line(skipped))
            lines.append("")
        result_parts: dict[str, Any] = {}
        for name, r in built.items():
            up = pr.parts[name].print_up
            placed = on_bed(r.solid, up, pr.bed)
            files = []
            with stage(f"запись детали {name}"):
                if format in ("3mf", "both"):
                    files.append(write_3mf(out_dir / f"{safe_name(name)}.3mf", placed,
                                           title=name, description=description))
                if format in ("stl", "both"):
                    files.append(write_stl(out_dir / f"{safe_name(name)}.stl", placed))
            ex = extents(placed)
            # Рецепт — из того, что посчитано в ЭТОЙ позе, а не на глаз: те же
            # функции, что у проверок печатности. Шаг растра растёт с деталью,
            # чтобы выгрузка большой детали не стоила минут.
            step = max(DEFAULT_STEP, max(ex) / 400)
            pose = up or "+Z"
            with stage(f"рецепт печати детали {name}"):
                sup = support_volume(r.solid, up=pose, step=step, name=name)
                bridge = bridge_span(r.solid, up=pose, step=step, name=name)
                first = bed_contact(r.solid, layer=layer, up=pose, name=name)
            rel = [str(f.path.relative_to(pr.root)) for f in files]
            lines.append(f"{name} → {', '.join(rel)}")
            lines.append(f"  поза: {up} вверх" if up else
                         "  поза: НЕ ОБЪЯВЛЕНА у детали — выгружено как построено "
                         "(+Z вверх); объявите @part(print_up=...)")
            lines.append(f"  на столе {ex[0]:.1f}×{ex[1]:.1f}×{ex[2]:.1f} мм, "
                         f"{files[0].triangles} треуг., объём {files[0].volume:.1f} мм³")
            if files[0].note:
                lines.append(f"  {files[0].note}")
            lines.append(f"  поддержки ≈ {sup.value:.0f} мм³, самый длинный мост "
                         f"{bridge.value:.1f} мм, первый слой {first.value:.0f} мм²"
                         + (" (в поддержки вошли и своды мостов — их слайсер "
                            "проходит без опор)" if bridge.value > 0 else ""))
            result_parts[name] = {
                "files": rel, "print_up": up, "extents_mm": [round(v, 3) for v in ex],
                "triangles": files[0].triangles, "volume_mm3": round(files[0].volume, 4),
                "precision": files[0].note,
                "support_mm3": sup.value, "bridge_mm": bridge.value,
                "first_layer_mm2": first.value,
            }
        lines.append("")
        lines.append(f"Печать: {printer.get('material', 'PLA')}, слой {layer:g} мм, "
                     f"сопло {float(printer.get('nozzle', 0.4)):g} мм (из datum.toml). "
                     f"Поддержки и мосты — по послойному правилу 45°, не по слайсеру.")
        return {"ok": True, "ready": not warnings, "warnings": warnings,
                "checks": verdict, "parts": result_parts, "skipped": skipped,
                "report": "\n".join(lines)}

    return _guard(run)


def _geom_event(name: str, r, view: dict) -> dict:
    """Событие «у детали новая геометрия» — одно и то же по ходу сборки и в
    результате (сервер узнаёт повтор по содержимому)."""
    return {"t": "geom.updated", "part": name, **view, "draft": r.draft,
            "volume_mm3": round(r.topology.volume, 4), "bodies": r.topology.bodies}


def _lesson_reminders(pr, printed: dict) -> list[str]:
    """Строки «опыт печати: …» для собранных печатных деталей. Беда с базой
    опыта сборку не роняет: это напоминание."""
    try:
        from datum.doc import lessons
        from datum.io.export import on_bed

        signed = [x for x in lessons.read() if x.sign and x.outcome != "хорошо"]
        if not signed:
            return []
        out = []
        for name, r in printed.items():
            if not (r.ok and r.solid is not None):
                continue
            posed = on_bed(r.solid, pr.parts[name].print_up, pr.bed)
            for lesson, measured in lessons.matching(posed, lessons=signed)[:3]:
                out.append(f"опыт печати — {name}: {lessons.describe_sign(lesson.sign)} "
                           f"({measured}). Урок {lesson.line()}")
        return out
    except Exception:                                # noqa: BLE001 — напоминание
        return []


def _printed_names(pr, parts: list[str] | None) -> tuple[list[str], list[str]]:
    """Что печатать и что пропустить: детали `printed=False` (покупное,
    показная сборка) в файл для слайсера не идут — ни по умолчанию, ни
    названные явно. Одно на `export` и `slice`. Неизвестное имя остаётся в
    списке: про него скажет сборка, как прежде."""
    wanted = list(parts) if parts else sorted(pr.parts)
    skipped = [n for n in wanted if n in pr.parts and not pr.parts[n].printed]
    return [n for n in wanted if n not in skipped], skipped


def _skipped_line(skipped: list[str]) -> str:
    return "не печатаются (printed=False), пропущены: " + ", ".join(skipped)


def _readiness(pr, checks: bool) -> tuple[list[str], str, str]:
    """Готов ли проект к печати: предупреждения, вердикт проверок, строка в файл.

    Одно на `export` и `slice`: две копии этой логики однажды разошлись бы, и
    «готов» в одном файле значило бы не то, что в другом.
    """
    summary = pr.params.summary()
    warnings = []
    if not summary["print_ready"]:
        warnings.append("не измерено (blocking): " + ", ".join(summary["blocking_unknowns"]))
    verdict = "проверки не прогонялись"
    if not checks:
        # Не прогоняли — значит и «готов» сказать нельзя.
        warnings.append("проверки не прогонялись — готовность не установлена")
    else:
        run_ = pr.run_checks()
        failed = [r.id for r in run_.results if r.status in ("fail", "error")]
        # Предупреждение печати не мешает, но молчать о нём нельзя: «все
        # прошли» при опрокидывающейся статуэтке было бы неправдой.
        warned = [r.id for r in run_.results if r.status == "warn"]
        if run_.empty:
            verdict = "проверок нет"
            warnings.append("проверок нет — выгружено непроверенное")
        elif failed:
            verdict = f"не прошли {len(failed)}: " + ", ".join(failed)
            warnings.append(verdict)
        elif warned:
            verdict = f"отказов нет из {len(run_.results)} проверок"
        else:
            verdict = f"все {len(run_.results)} проверок прошли"
        if warned and not run_.empty:
            verdict += f"; предупреждений {len(warned)}: " + ", ".join(warned)
    description = ("ГОТОВ К ПЕЧАТИ" if not warnings else
                   "НЕ ГОТОВ К ПЕЧАТИ — " + "; ".join(warnings)) + f". Проверки: {verdict}"
    return warnings, verdict, description


@op("slice", cli_name="slice", group="сборка",
    isolate=True, danger="mutates", memory_headroom_mb=4096,
    title="Нарезать в ElegooSlicer",
    summary=("Нарезать детали консольным ElegooSlicer: проект 3MF с профилями "
             "принтера, процесса и пластика (по datum.toml) и G-code — в output/. "
             "Профили разрешаются по манифесту Elegoo; нарезанное сверяется с "
             "заданным (профили, слой, тип стола, правки, число треугольников), "
             "не сошлось — файлов нет. Время печати и граммы — от слайсера; в отчёте "
             "— что он сделал по слоям (острова в воздухе, нависающие стенки, мосты) "
             "и расхождения с проверками Datum. Нет "
             "слайсера — отказ с причиной: тогда export и окно слайсера."),
    exit_code=lambda r: 1 if r.get("error") else 0)
async def slice_parts(
    project: Project,
    parts: Annotated[list[str] | None, Arg(help="имена деталей; пусто — все",
                                           metavar="ДЕТАЛЬ")] = None,
    process: Annotated[str | None, Arg(help="профиль процесса по имени; пусто — по "
                                            "слою из datum.toml, «Standard»")] = None,
    filament: Annotated[str | None, Arg(help="профиль пластика по имени; пусто — "
                                             "«Elegoo <material> @…» из datum.toml")] = None,
    overrides: Annotated[dict[str, str] | None,
                         Arg(help="правки профиля: {\"wall_loops\": \"4\", "
                                  "\"enable_support\": \"1\"} — только существующие "
                                  "ключи")] = None,
    checks: Annotated[bool, Arg(help="прогнать проверки ради вердикта в отчёте")] = True,
) -> dict[str, Any]:
    """Нарезка — с той же позой и той же сверкой, что у `export`.

    Деталь пишется проверенным 3MF (`io.export.write_3mf`, вершины уже
    разведены), и слайсер обязан нарезать ровно её треугольники. Правки к
    профилю выводятся не здесь, а человеком или агентом — с причиной: поддержки
    и кайма зависят от того, что Datum не видит (стол, пластик, опыт).
    """
    def run():
        from datum.checks.dfam import DEFAULT_STEP, support_volume
        from datum.geom.measure import extents
        from datum.io.export import on_bed, safe_name, write_3mf
        from datum.io.slicer import Profiles, SlicerError, choose, slice_model, slicer_command

        pr = open_project(project)
        printer = pr.manifest.get("printer", {})
        try:
            command = slicer_command()
            profiles = Profiles()
            choice = choose(profiles, printer_profile=str(printer.get("profile", "elegoo-cc2-0.4")),
                            nozzle=float(printer.get("nozzle", 0.4)),
                            layer=float(printer.get("layer", 0.2)),
                            material=str(printer.get("material", "PLA")),
                            process=process, filament=filament)
        except SlicerError as e:
            return _err(str(e))
        names, skipped = _printed_names(pr, parts)
        if not names:
            return _err("резать нечего: " + _skipped_line(skipped))
        built = pr.build(names)
        broken = [r.line() for r in built.values() if not r.ok]
        if broken:
            return _err("не собрались детали — резать нечего:\n" + "\n".join(broken))
        warnings, verdict, description = _readiness(pr, checks)
        out_dir = pr.root / "output"
        work = pr.root / ".datum" / "slice"
        material = safe_name(str(printer.get("material", "PLA")))
        lines = []
        if warnings:
            lines.append("ВНИМАНИЕ: проект не готов к печати — нарезано всё равно:")
            lines += [f"  • {w}" for w in warnings]
            lines.append("")
        lines.append(f"принтер {choice.printer}; процесс {choice.process}; "
                     f"пластик {choice.filament}"
                     + (f"; стол {choice.bed_type}" if choice.bed_type else ""))
        lines += [f"  ({n})" for n in choice.notes]
        if skipped:
            lines.append(_skipped_line(skipped))
        if overrides:
            lines.append("правки: " + ", ".join(f"{k}={v}" for k, v in overrides.items()))
        result_parts: dict[str, Any] = {}
        for name, r in built.items():
            up = pr.parts[name].print_up
            placed = on_bed(r.solid, up, pr.bed)
            work.mkdir(parents=True, exist_ok=True)
            model = write_3mf(work / f"{safe_name(name)}.datum.3mf", placed,
                              title=name, description=description)
            # Настройки печати детали (стенки, заполнение…) — те же, по которым
            # Datum считал её массу для симуляции; явные правки вызова — поверх.
            part_over = pr.print_settings(name).slicer_overrides()
            merged = {**part_over, **(overrides or {})} or None
            try:
                stem = f"{safe_name(name)}_{material}"
                cut = slice_model(model.path, out_dir / f"{stem}.3mf", out_dir / f"{stem}.gcode",
                                  profiles=profiles, choice=choice, triangles=model.triangles,
                                  overrides=merged, work=work, command=command)
            except SlicerError as e:
                return _err(f"{name}: {e}")
            finally:
                model.path.unlink(missing_ok=True)
            ex = extents(placed)
            sup = support_volume(r.solid, up=up or "+Z", step=max(DEFAULT_STEP, max(ex) / 400),
                                 name=name)
            rel = [str(p.relative_to(pr.root)) for p in (cut.project, cut.gcode)]
            lines.append(f"{name} → {', '.join(rel)}")
            if part_over:
                lines.append("  настройки детали: " + ", ".join(f"{k}={v}" for k, v in part_over.items()))
            # Сверка модели массы со слайсером — на каждой нарезке: по этой
            # массе считается симуляция, и расхождение должно быть видно.
            mass_note = ""
            try:
                from datum.fab.mass import cached_print_mass
                from datum.io.slicer import part_grams

                settings = pr.print_settings(name)
                est = cached_print_mass(r.solid, settings, up=up,
                                        cache_dir=pr.root / ".datum" / "mass").mass_g
                real = part_grams(cut.gcode, density=float(cut.config.get(
                    "filament_density", settings.density_g_cm3)))
                if real > 0:
                    mass_note = (f"  масса детали: Datum {est:.1f} г, слайсер {real:.1f} г "
                                 f"({(est / real - 1) * 100:+.1f} %; без прочистки и каймы)")
            except Exception as e:                       # сверка — справка, не повод отказать
                mass_note = f"  масса детали: сверить не вышло ({type(e).__name__}: {e})"
            if mass_note:
                lines.append(mass_note)
            lines.append(f"  слайсер: {cut.time}, {cut.grams:.1f} г, слоёв {cut.layers}, "
                         f"{cut.triangles} треуг. — как у детали")
            lines.append(f"  поддержки по правилу 45° Datum ≈ {sup.value:.0f} мм³; в профиле "
                         f"enable_support = {cut.config.get('enable_support', '?')}")
            if model.note:
                lines.append(f"  {model.note}")
            # Тело G-code: что слайсер сделал по слоям (острова в воздухе,
            # нависающие стенки, мосты) и где это расходится с проверками
            # Datum. Справка: не разобралось — нарезка остаётся.
            body: dict[str, Any] = {}
            body_warnings: list[str] = []
            try:
                from datum.tools.ops_gcode import gcode_findings

                body_lines, body_warnings, body = gcode_findings(
                    r.solid, up, cut.gcode, cut.config, name)
                lines += [f"  {line}" for line in body_lines]
            except Exception as e:                       # noqa: BLE001 — справка
                lines.append(f"  тело G-code разобрать не вышло ({type(e).__name__}: {e})")
            lines += [f"  ! {w}" for w in [*cut.warnings, *body_warnings]]
            result_parts[name] = {"files": rel, "time": cut.time, "grams": cut.grams,
                                  "layers": cut.layers, "triangles": cut.triangles,
                                  "warnings": [*cut.warnings, *body_warnings],
                                  "support_mm3": sup.value, "gcode": body}
        lines.append("")
        lines.append("Проект открывается в окне ElegooSlicer как свой; G-code — для печати.")
        return {"ok": True, "ready": not warnings, "warnings": warnings, "checks": verdict,
                "choice": {"printer": choice.printer, "process": choice.process,
                           "filament": choice.filament, "bed_type": choice.bed_type},
                "parts": result_parts, "skipped": skipped, "report": "\n".join(lines)}

    return _guard(run)
