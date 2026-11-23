"""Проверки, МКЭ и развёртка по незамеренному.

Часть операций Datum (`tools/ops.py` — их общий вход: оттуда строятся
MCP-инструменты и команды CLI). Соглашение то же: операция возвращает
словарь, `report` — текст для человека, об ошибке — ключ `error`.
"""

from __future__ import annotations

from typing import Annotated, Any

from datum.tools.ops_base import Direction, Face, Project, _err, _guard, open_project
from datum.tools.registry import Arg, op


# ------------------------------------------------------------------ проверки
@op("check_run", cli_name="check", group="проверки",
    isolate=True,
    title="Прогнать проверки",
    summary=("Прогнать проверки проекта и вернуть таблицу с измеренными значениями и "
             "порогами. Показывает, где конструкция ближе всего к краю и что "
             "изменилось с прошлого прогона. Без фильтра — все проверки и вердикт "
             "проекта; only / parts / group — только выбранные (быстрее, но это "
             "НЕ вердикт проекта)."),
    exit_code=lambda r: 1 if (r.get("error") or not r.get("ok", False)) else 0)
async def check_run(
    project: Project,
    only: Annotated[list[str] | None,
                    Arg(help="id проверок или шаблоны (крышка.*); пусто — все",
                        metavar="ID")] = None,
    parts: Annotated[list[str] | None,
                     Arg(help="только проверки, где участвуют эти детали",
                         metavar="ДЕТАЛЬ")] = None,
    group: Annotated[str, Arg(help="только группа: motion, printing, fit, sim…")] = "",
    fresh: Annotated[bool, Arg(help="пересчитать всё заново, не беря из кеша проверки, "
                                    "входы которых не менялись")] = False,
) -> dict[str, Any]:
    def run():
        from datum.doc import lastrun

        pr = open_project(project)
        selected = bool(only or parts or group)
        total = len(pr.specs()) if selected else 0
        run_ = pr.run_checks(only=only or None, parts=parts or None, group=group,
                             fresh=fresh)
        d = run_.as_dict()
        text = run_.report()
        if selected:
            # «ИТОГ: OK» у выбранного читался бы как вердикт проекта.
            what = ", ".join(x for x in (
                f"only={only}" if only else "", f"parts={parts}" if parts else "",
                f"group={group}" if group else "") if x)
            head = (f"ВЫБОРОЧНЫЙ прогон ({what}): результатов {len(run_.results)}, "
                    f"объявлений в проекте {total}. Это НЕ вердикт проекта — он у "
                    f"прогона без фильтра.")
            text = head + "\n" + text.replace("ИТОГ:", "ИТОГ ВЫБРАННОГО:")
            d["selected"] = {"only": only or [], "parts": parts or [], "group": group,
                             "declared": total}
        if run_.worst and run_.ok and run_.worst.margin is not None:
            # Запас — доля порога, поэтому в процентах: «+0.00» у отсека,
            # сделанного ровно в объявленный размер, читалось как ноль.
            text += (f"\n\nБлиже всего к краю: {run_.worst.id} "
                     f"(запас {run_.worst.margin * 100:+.1f} % от порога)")
        # Что изменилось с прошлого прогона — агент считал это своим Python
        # после каждого вызова (разбор сессий), человеку нужно то же самое.
        delta = lastrun.changes(pr.root, run_)
        d["changes"] = {k: delta[k] for k in ("worse", "better", "moved", "new")}
        if delta["lines"]:
            text += "\n\nС прошлого прогона:\n" + "\n".join(delta["lines"])
        elif delta["known"] and run_.results:
            text += "\n\nС прошлого прогона вердикты не изменились."
        lastrun.remember(pr.root, run_, full=not selected)
        # Сколько взято из кеша — словами: «прошло за секунду» не должно
        # читаться как «ничего не проверялось».
        reused, asked = pr.check_cache_use
        d["cached"] = {"reused": reused, "specs": asked}
        if reused:
            text += (f"\n\nИз кеша: {reused} из {asked} проверок — их входы (параметры, "
                     f"детали, код, файлы проекта) не менялись. Пересчитать все — fresh=true.")
        # Объявлен, но не прочитан — правят его, а деталь не меняется. Не
        # провал (вердикт проверок тот же), а предупреждение с именами. Судить
        # можно только по полному прогону: выборочный читает не всё.
        unread = None if selected else pr.unread_params(run_)
        d["unread_params"] = unread
        if unread:
            text += ("\n\n! Параметры, которых не читает ни деталь, ни проверка: "
                     + ", ".join(unread) + " — используйте или уберите")
        d["report"] = text
        return d

    return _guard(run)


@op("check_list", cli_name="checks", group="проверки",
    title="Каталог проверок",
    summary="Какие проверки можно объявить в checks/checks.yaml, что каждая "
            "измеряет и — главное — что принимает на веру.")
async def check_list(
    names: Annotated[list[str] | None,
                     Arg(help="имена проверок или шаблоны (sim_*); пусто — все",
                         metavar="ИМЯ")] = None,
    group: Annotated[str, Arg(help="только группа: motion, printing, fit, machine, sim…")] = "",
) -> dict[str, Any]:
    """Каталог с ДОПУЩЕНИЯМИ, а не только с описаниями.

    Допущение важнее описания. Механизмов не перечислить, и выбрать проверку
    по названию — верный способ получить уверенный неверный ответ: так
    `assembly_path` объявляет защёлку несобираемой, будучи совершенно права
    буквально. Поэтому рядом с каждой строкой стоит, где она врёт.
    """
    import inspect

    from datum.checks.library import LIBRARY

    def arguments(e) -> dict[str, Any]:
        """Аргументы для checks.yaml: имя → умолчание или «обязателен». Прежде
        их не было вовсе — агент угадывал (`obstacles` вместо `others`, `t`
        вместо `thickness`) и узнавал подпись из текста отказа (найдено обзором)."""
        out: dict[str, Any] = {}
        for name, param in inspect.signature(e.fn).parameters.items():
            if name in ("id", "name", "depends_on") or param.kind in (
                    param.VAR_POSITIONAL, param.VAR_KEYWORD):
                continue
            default = param.default
            if isinstance(default, float) and default in (float("inf"), float("-inf")):
                default = "без предела"         # бесконечность — не JSON
            out[name] = ("обязателен" if default is inspect.Parameter.empty else
                         default if isinstance(default, (int, float, str, bool, type(None)))
                         else repr(default))
        return out

    def shown(args: dict[str, Any]) -> str:
        return ", ".join(f"{k}" if v == "обязателен" else f"{k}={v!r}" for k, v in args.items())

    chosen = dict(sorted(LIBRARY.items()))
    groups = sorted({e.group for e in LIBRARY.values()})
    if group:
        if group not in groups:
            return _err(f"нет группы проверок {group!r} — группы каталога такие — "
                        f"{', '.join(groups)}")
        chosen = {n: e for n, e in chosen.items() if e.group == group}
    if names:
        import fnmatch

        unknown = [n for n in names if not fnmatch.filter(list(LIBRARY), n)]
        if unknown:
            from datum.build.hints import CHECK_ALIASES, names_hint

            tip = names_hint([str(n) for n in unknown], LIBRARY, aliases=CHECK_ALIASES)
            return _err(f"нет таких проверок: {unknown}"
                        + (f" (подсказка: {tip})" if tip else "")
                        + f"; каталог: {sorted(LIBRARY)}")
        chosen = {n: e for n, e in chosen.items()
                  if any(fnmatch.fnmatchcase(n, pat) for pat in names)}
    lines = []
    for n, e in chosen.items():
        lines.append(f"  {n:<24} {e.group:<9} {e.doc[:60]}")
        lines.append(f"  {'':<24} аргументы: {shown(arguments(e))}")
        lines.append(f"  {'':<24} принимает на веру: {e.assumes}")
    whole = len(chosen) == len(LIBRARY)
    head = (f"Проверок в каталоге: {len(LIBRARY)}." if whole else
            f"Проверок выбрано: {len(chosen)} из {len(LIBRARY)} (группы: {', '.join(groups)}).")
    return {"checks": {n: {"group": e.group, "doc": e.doc,
                           "assumes": e.assumes,
                           "part_args": list(e.solid_args + e.fn_args),
                           "args": arguments(e)}
                       for n, e in chosen.items()},
            "groups": groups,
            "report": (f"{head} У каждой указано, "
                       f"что она принимает на веру — то есть где неприменима.\n"
                       + "\n".join(lines))}


@op("fem_verify", cli_name="fem-verify", group="проверки",
    # Без кода возврата `make fem` (и `make check`) был зелёным при
    # проваленной самопроверке решателя (аудит 2026-11-21).
    exit_code=lambda r: 1 if (r.get("error") or not r.get("ok", False)) else 0,
    title="Самопроверка решателя МКЭ",
    summary=("Воспроизводит ли решатель задачи с ТОЧНЫМ ответом. Пока не "
             "воспроизводит — числам про настоящую деталь верить нельзя."))
async def fem_verify(
    modulus: Annotated[float, Arg(help="модуль упругости, МПа")],
    poisson: Annotated[float, Arg(help="коэффициент Пуассона")] = 0.35,
    material: Annotated[str, Arg(help="название материала для отчёта")] = "",
) -> dict[str, Any]:
    """Прогнать эталоны с точным решением.

    Свойства материала приходят аргументами, а не берутся из таблицы: у
    печатного пластика они зависят от ориентации слоёв и партии, и подставить
    число значило бы решить за человека. Но сам ОТВЕТ от них почти не зависит:
    эталоны точны при любом разумном материале, и это как раз то, что
    проверяется.
    """
    from datum.fem.model import FemError, Material
    from datum.fem.verify import verify

    try:
        report = verify(Material(modulus, poisson, material))
    except FemError as e:
        return {"error": str(e), "report": f"ОШИБКА: {e}"}
    return {"ok": report.ok,
            "cases": [{"name": c.name, "error": c.error, "tolerance": c.tolerance,
                       "dofs": c.dofs, "ms": round(c.ms, 1), "ok": c.ok}
                      for c in report.cases],
            "report": report.report()}


#: Длина ребра сетки МКЭ, доля диагонали (см. `fem_solve`).
FEM_EDGE_RANGE = (0.01, 0.5)
#: Сила — не больше тысячи тонн: дальше не деталь, а ошибка в единицах.
FEM_FORCE_MAX_N = 1e7


@op("fem_solve", cli_name="fem", group="проверки",
    # Тетраэдральная сетка произвольной детали (fTetWild) и разреженная матрица
    # жёсткости — не скрипт детали: на детали с резьбой запас в 1 ГБ кончался
    # MemoryError при любом шаге сетки. Запас как у операций над чужими мешами.
    isolate=True, memory_headroom_mb=4096,
    title="Расчёт детали под нагрузкой",
    summary=("Деформация и напряжение в ПРОИЗВОЛЬНОЙ детали: тетраэдральная "
             "сетка плюс трёхмерный решатель. Перед ответом решатель "
             "доказывает себя на задачах с точным решением, и результат "
             "приходит вместе с этими числами."))
async def fem_solve(
    project: Project,
    part: Annotated[str, Arg(help="деталь")],
    modulus: Annotated[float, Arg(help="модуль упругости, МПа")],
    force: Annotated[float, Arg(help="сила, Н")],
    fixed: Annotated[Face, Arg(help="закреплённая грань: ось_min или ось_max")] = "z_min",
    load: Annotated[Face, Arg(help="нагруженная грань")] = "z_max",
    along: Annotated[Direction, Arg(help="куда давит сила; в командной строке пишите "
                                         "--along=-Z, иначе минус примут за ключ")] = "-Z",
    poisson: Annotated[float, Arg(help="коэффициент Пуассона")] = 0.35,
    edge: Annotated[float, Arg(help="длина ребра сетки, доля диагонали")] = 0.05,
    tolerance: Annotated[float, Arg(help="толщина слоя отбора грани, мм; 0 — по умолчанию: два конверта мешера")] = 0.0,
    fixed_region: Annotated[list[float] | None, Arg(
        help="участок закреплённой грани: x0,y0,z0,x1,y1,z1 (пятно, а не вся грань)",
        metavar="X0,Y0,Z0,X1,Y1,Z1")] = None,
    load_region: Annotated[list[float] | None, Arg(
        help="участок нагруженной грани: x0,y0,z0,x1,y1,z1 (пятно под винтом, контакт)",
        metavar="X0,Y0,Z0,X1,Y1,Z1")] = None,
) -> dict[str, Any]:
    """Посчитать деталь — и показать, на каком основании числу можно верить.

    ПОРЯДОК ЗДЕСЬ НЕ СЛУЧАЕН. Сперва строится сетка, потом на НЕЙ ЖЕ решатель
    прогоняет тест заплаты (точное решение на всей границе обязано
    воспроизвестись внутри), и только потом считается сама задача. Эталон на
    другой сетке ничего не доказал бы про эту: у неструктурированной сетки
    качество тетраэдров разное, и испортить его умеет именно геометрия детали.

    Свойства материала приходят аргументами, а не из таблицы: у печатного
    пластика они зависят от ориентации слоёв и партии.
    """
    import numpy as np

    from datum.checks.motion import direction as parse_direction
    from datum.fem.model import FemError, Material
    from datum.fem.solve import solve_faces
    from datum.fem.tetmesh import mesher_available, tet_mesh
    from datum.fem.verify import patch_test

    def run():
        # Мешер fTetWild на ребре 0.001 диагонали падал с std::bad_alloc, на
        # 1e308 — с SIGSEGV, и оба раза уже после сборки детали. Мельче 0.01
        # он работает минуты (замерено: 0.005 на пластине 55×35×3 — дольше
        # двух минут), крупнее 0.5 сетка уже не про деталь.
        lo, hi = FEM_EDGE_RANGE
        if not lo <= edge <= hi:
            return _err(f"edge {edge:g} — длина ребра сетки берётся долей диагонали от "
                        f"{lo:g} до {hi:g}; умолчание 0.05, точнее — 0.03")
        if abs(force) > FEM_FORCE_MAX_N:
            return _err(f"force {force:g} Н — больше {FEM_FORCE_MAX_N:g} Н (тысяча тонн): "
                        f"проверьте единицы, сила в ньютонах")
        if tolerance < 0:
            return _err(f"tolerance {tolerance:g} мм — толщина слоя отбора не бывает "
                        f"отрицательной; 0 — по умолчанию")
        if not mesher_available():
            return _err("тетраэдрального мешера нет: поставьте `pip install "
                        "pytetwild` (pyvista при этом НЕ нужен)")
        pr = open_project(project)
        built = pr.build([part])
        result = built[part]
        if not result.ok:
            return _err(f"деталь не собралась: {result.line()}")

        try:
            material = Material(modulus, poisson)
            mesh = tet_mesh(result.solid, edge_relative=edge)
            check = patch_test(mesh.skfem(), material)
            if not check.ok:
                return _err("решатель не воспроизвёл эталоны на ЭТОЙ сетке, "
                            f"числам верить нельзя.\n{check.report()}")
            vector = parse_direction(along) * float(force)
            load_vec = tuple(float(v) for v in vector)
            regions = dict(fixed_region=fixed_region, load_region=load_region)
            solution = solve_faces(mesh, material, fixed=fixed, load=load,
                                   force=load_vec, tolerance=tolerance or None, **regions)
            # ВТОРАЯ СЕТКА, вдвое грубее. Тест заплаты доказывает, что верен
            # ЭЛЕМЕНТ, — на любой невырожденной сетке он тождество. О том,
            # сошлась ли СЕТКА, он не говорит ничего: на консоли через fTetWild
            # прогиб при ребре 0.1 занижен на 4.6 %, при 0.05 — на 1.2 %, а
            # эталоны в обоих случаях 1e-15. Одно число с пятью знаками рядом с
            # «эталоны 1e-15» — ровно уверенная цифра неизвестной верности.
            coarse_mesh = tet_mesh(result.solid, edge_relative=edge * 2)
            coarse = solve_faces(coarse_mesh, material, fixed=fixed, load=load,
                                 force=load_vec, tolerance=tolerance or None, **regions)
        except FemError as e:
            return _err(str(e))

        def peak_of(sol) -> float:
            return float(np.sqrt(sum(sol.component(i) ** 2 for i in range(3))).max())

        peak, peak_coarse = peak_of(solution), peak_of(coarse)
        # Вилка как у проверок: МКЭ жёстче точного, истина — по ходу сходимости,
        # остаток взят как при первом порядке (порядок по двум сеткам не оценить).
        far = peak + (peak - peak_coarse)
        band = tuple(sorted((peak, far)))
        change = abs(peak - peak_coarse) / max(peak, 1e-30)
        strain_change = abs(solution.peak_strain - coarse.peak_strain) / max(
            solution.peak_strain, 1e-30)
        growth = (solution.peak_strain - coarse.peak_strain) / max(coarse.peak_strain, 1e-30)
        p99_change = abs(solution.strain_p99 - coarse.strain_p99) / max(solution.strain_p99, 1e-30)
        lo, hi = mesh.points.min(axis=0), mesh.points.max(axis=0)
        cell = edge * float(np.linalg.norm(hi - lo))          # ребро сетки, мм
        axis = {"x": 0, "y": 1, "z": 2}[fixed.split("_")[0].lower()]
        fixed_at = hi[axis] if fixed.endswith("max") else lo[axis]
        near_fixed = abs(solution.peak_at[axis] - fixed_at) <= 2 * cell
        where = ", ".join(f"{c:.1f}" for c in solution.peak_at)
        # СИНГУЛЯРНОСТЬ. Пик в остром внутреннем углу или на кромке заделки
        # растёт при измельчении без предела — это свойство модели, а не
        # детали. Признак: пик вырос, а смещение и 99-й процентиль сошлись.
        # Сравнивать такой пик с допускаемым нельзя — в методике конструктора
        # это первое, что проверяют, прежде чем поверить максимуму.
        # Порог роста — из теории, а не подгонкой. У острого внутреннего угла
        # 90° напряжение растёт как r^−0.456, и при измельчении вдвое пик
        # вырастает в 2^0.456 ≈ 1.37 раза; замерено на уголке 57…87 %. У того же
        # уголка со скруглением R3 пик на грубой сетке гуляет до 9 % — плохо
        # разрешённое скругление, а не особенность. 20 % разделяет их с запасом.
        # Процентиль шевелится и сам, но медленнее пика во много раз.
        singular = growth > 0.20 and change < 0.05 and growth > 3 * p99_change
        corner = _near_concave_edge(result.solid, solution.peak_at, 2 * cell)
        if strain_change < 0.05:
            strain_note = "сходится"
        elif singular:
            strain_note = (
                f"пик РАСТЁТ с измельчением ({coarse.peak_strain:.6f} → "
                f"{solution.peak_strain:.6f}), а смещение и 99-й процентиль сошлись — "
                f"СИНГУЛЯРНОСТЬ в точке ({where})"
                + (", у острого внутреннего угла детали" if corner else
                   ", у кромки заделки" if near_fixed else "")
                + ". Это свойство сетки, а не детали: пик с допускаемым не сравнивают. "
                  "99-й процентиль — оценка тела в стороне от угла, но не замена "
                  "расчёта: в модель — реальное скругление, и пересчитать")
        else:
            strain_note = (
                f"НЕ СХОДИТСЯ (на грубой {coarse.peak_strain:.6f}, расхождение "
                f"{strain_change * 100:.0f} %, пик в ({where})"
                f"{', у острого внутреннего угла' if corner else ', у кромки заделки' if near_fixed else ''}"
                f"): числу верить нельзя — "
                f"уменьшите --edge")

        # Защемлена ли грань, а не три треугольника.
        warnings: list[str] = []
        if change > 0.05:
            warnings.append(
                f"сетка не сошлась: между рёбрами {edge * 2:g} и {edge:g} ответ сдвинулся "
                f"на {change * 100:.0f} %, и вилка при таком шаге — оценка, а не граница. "
                f"Уменьшите --edge (например до {edge / 2:g}) и сравните")
        others = [i for i in range(3) if i != axis]
        projected = float((hi[others[0]] - lo[others[0]]) * (hi[others[1]] - lo[others[1]]))
        share = solution.fixed_area / projected if projected > 0 else 0.0
        if share < 0.10 and fixed_region is None:
            warnings.append(
                f"защемлено {solution.fixed_area:.2f} мм² — {share * 100:.1f} % проекции "
                f"детали на {fixed}. Если это не задуманная малая опора, тело там не "
                f"плоское или допуск мал (`tolerance`), и заделка почти точечная: "
                f"смещения вблизи неё нефизичны")

        return {
            "part": part,
            "mesh": {"nodes": mesh.nodes, "cells": mesh.cells,
                     "worst_quality": round(mesh.worst_quality, 4),
                     "median_quality": round(mesh.median_quality, 4),
                     "volume_mm3": round(mesh.volume, 3)},
            "self_check": {c.name: c.error for c in check.cases},
            "faces": {"fixed_facets": solution.fixed_facets,
                      "fixed_area_mm2": round(solution.fixed_area, 3),
                      "fixed_share": round(share, 4),
                      "load_facets": solution.load_facets,
                      "load_area_mm2": round(solution.load_area, 3),
                      "tolerance_mm": round(solution.face_tolerance, 5)},
            "max_displacement_mm": round(peak, 5),
            "max_displacement_band_mm": [round(v, 5) for v in band],
            "coarse_max_displacement_mm": round(peak_coarse, 5),
            "mesh_change": round(change, 5),
            "peak_strain": round(solution.peak_strain, 7),
            "peak_strain_coarse": round(coarse.peak_strain, 7),
            "peak_strain_converged": strain_change < 0.05,
            "peak_strain_at": [round(c, 3) for c in solution.peak_at],
            "peak_strain_singular": singular,
            "strain_p99": round(solution.strain_p99, 7),
            "strain_p99_coarse": round(coarse.strain_p99, 7),
            "warnings": warnings,
            "dofs": solution.dofs,
            "ms": round(solution.ms + coarse.ms, 1),
            "report": (
                f"{part}: {fixed}{f' в {list(fixed_region)}' if fixed_region else ''} закреплено, "
                f"на {load}{f' в {list(load_region)}' if load_region else ''} сила "
                f"{force:g} Н по {along}\n"
                f"  сетка:      {mesh.line()}\n"
                f"  грани:      защемлено {solution.fixed_area:.2f} мм² "
                f"({solution.fixed_facets} гр., {share * 100:.0f} % проекции), нагружено "
                f"{solution.load_area:.2f} мм² ({solution.load_facets} гр.), допуск "
                f"{solution.face_tolerance:.4f} мм\n"
                f"  эталоны:    тест заплаты на этой сетке "
                + ", ".join(f"{c.name.replace('заплата: ', '')} {c.error:.0e}"
                            for c in check.cases)
                + " — проверяет элемент, а НЕ сходимость сетки\n"
                f"  смещение:   {band[0]:.4f}…{band[1]:.4f} мм наибольшее (ребро {edge:g}: "
                f"{peak:.4f}; вдвое грубее: {peak_coarse:.4f}; шевелится на "
                f"{change * 100:.1f} %, порядок по двум сеткам не оценить)\n"
                f"  деформация: {solution.peak_strain:.6f} наибольшая главная — "
                f"{strain_note} ({solution.dofs} степ. свободы); 99-й процентиль "
                f"{solution.strain_p99:.6f} (на грубой {coarse.strain_p99:.6f})"
                + "".join(f"\n  ВНИМАНИЕ:   {w}" for w in warnings)),
        }

    return _guard(run)


def _near_concave_edge(solid, point, radius: float, min_angle_deg: float = 20.0) -> bool:
    """Есть ли у детали вогнутое ребро (внутренний угол) ближе `radius` к точке.

    Вогнутое — где поверхность загибается ВНУТРЬ тела; угол между гранями
    больше `min_angle_deg`, чтобы шум триангуляции гладкой поверхности не
    считался углом. Именно там упругое решение особое.
    """
    import numpy as np
    import trimesh

    from datum.geom.convert import vertices_faces

    verts, faces = vertices_faces(solid.manifold)
    mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
    pick = (~mesh.face_adjacency_convex) & (mesh.face_adjacency_angles > np.radians(min_angle_deg))
    if not pick.any():
        return False
    ends = mesh.vertices[mesh.face_adjacency_edges[pick]]          # (n, 2, 3)
    a, b = ends[:, 0], ends[:, 1]
    p = np.asarray(point, dtype=float)
    ab = b - a
    t = np.clip(((p - a) * ab).sum(1) / np.maximum((ab * ab).sum(1), 1e-30), 0.0, 1.0)
    dist = np.linalg.norm(a + t[:, None] * ab - p, axis=1)
    return bool(dist.min() <= radius)


@op("sweep_assumed", cli_name="sweep", group="проверки",
    isolate=True,
    title="Что измерить в первую очередь",
    summary=("Прогнать проверки по краям диапазонов угаданных параметров и показать, "
             "какое неизвестное решает дело, а какие ни на что не влияют. Ответ на "
             "вопрос «что идти мерить»."))
async def sweep_assumed(
    project: Project,
    params: Annotated[list[str] | None, Arg(help="ограничить перечисленными",
                                            metavar="ИМЯ")] = None,
) -> dict[str, Any]:
    def run():
        s = open_project(project).sweep(params or None)
        d = s.as_dict()
        d["report"] = s.report()
        return d

    return _guard(run)
