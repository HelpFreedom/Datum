"""Операции симуляции и массы печати: `sim_list`, `sim_run`, `sim_stress`, `part_mass`.

Отдельным модулем, чтобы `ops.py` не рос: регистрируются тем же `@op` и
подключаются из него. Все три изолированы — исполняют скрипты проекта.
"""

from __future__ import annotations

from typing import Annotated, Any

import numpy as np

from datum.tools.ops import Project, _blob_rel, _err, _guard, open_project
from datum.tools.registry import Arg, op

__all__: list[str] = []


def _mech_summary(pr, name: str) -> dict:
    comp = pr.compiled_mechanism(name)
    mech = comp.mechanism
    links = []
    for lname, lk in comp.links.items():
        j = lk.joint
        links.append({
            "name": lname, "parent": lk.parent, "mass_g": round(lk.mass_kg * 1000, 2),
            "com_mm": [round(float(v) * 1000, 2) for v in lk.com_m],
            "joint": None if j is None else {
                "kind": j.kind, "axis": list(j.axis), "origin": list(j.origin),
                "range": list(j.limits),
                "follows": None if j.follows is None else {"leader": j.follows[0],
                                                           "ratio": j.follows[1]},
                "drive": None if j.drive is None else {"limit": j.drive.limit, "speed": j.drive.speed,
                                                       "accel": j.drive.max_accel,
                                                       "unit": j.effort_unit}},
            "pieces": lk.pieces})
    return {"name": name, "base": mech.base, "friction": mech.friction,
            "mass_g": round(comp.total_mass_kg * 1000, 2), "links": links,
            "payloads": [{"name": p.name, "link": p.link, "mass_g": p.mass_g, "at": list(p.at)}
                         for p in mech.payloads.values()],
            "scenarios": [{"name": s.name, "poses": len(s.poses), "payloads": list(s.payloads),
                           "hold_s": s.hold_s, "speed_scale": s.speed_scale}
                          for s in mech.scenarios.values()],
            "file": str(pr.mechanism_files.get(name, ""))}


@op("sim_list", cli_name="sim-list", group="симуляция", isolate=True,
    title="Механизмы проекта",
    summary=("Механизмы (@mechanism): звенья, суставы, приводы, грузы, сценарии — и МАССА "
             "каждого звена с источником: печатная деталь по её настройкам печати или "
             "покупное с заданной массой. С этого начинать симуляцию."))
async def sim_list(project: Project) -> dict[str, Any]:
    def run():
        pr = open_project(project)
        if not pr.mechanisms:
            return {"mechanisms": [], "report": (
                "механизмов нет. Объявите @mechanism в parts/*.py — когда и как: "
                "справочник навыка references/simulation.md")}
        out, lines = [], []
        for name in sorted(pr.mechanisms):
            m = _mech_summary(pr, name)
            out.append(m)
            lines.append(f"{name}: основание {m['base']}, масса {m['mass_g']:.0f} г")
            for lk in m["links"]:
                j = lk["joint"]
                jtxt = "приварено" if j is None and lk["parent"] else "основание" if j is None else (
                    f"{j['kind']} {j['range'][0]:g}…{j['range'][1]:g}"
                    + (f", ведомый: ×{j['follows']['ratio']:g} от {j['follows']['leader']}"
                       if j["follows"] else
                       f", привод {j['drive']['limit']:g} {j['drive']['unit']} "
                       f"{j['drive']['speed']:g}/с" if j["drive"] else ", БЕЗ привода"))
                lines.append(f"  {lk['name']}: {lk['mass_g']:.1f} г, {jtxt}")
                for p in lk["pieces"]:
                    lines.append(f"      {p['label']}: {p['mass_g']:.1f} г — {p['source']}")
            for p in m["payloads"]:
                lines.append(f"  груз {p['name']}: {p['mass_g']:g} г на {p['link']}")
            for s in m["scenarios"]:
                lines.append(f"  сценарий {s['name']}: поз {s['poses']}"
                             + (f", грузы {', '.join(s['payloads'])}" if s["payloads"] else ""))
        return {"mechanisms": out, "report": "\n".join(lines)}

    return _guard(run)


def _key_instants(result) -> list[tuple[str, int]]:
    """Моменты для ленты: начало, худший момент привода, худшее основание, конец."""
    picks = [("начало", 0)]
    need = np.abs(result.required)
    if need.size:
        i = int(np.unravel_index(np.argmax(need), need.shape)[0])
        k = int(np.unravel_index(np.argmax(need), need.shape)[1])
        picks.append((f"пик {result.plan.drivers[k]}", i))
    if result.tip_margin_mm is not None:
        picks.append(("ближе к краю опоры", int(np.argmin(result.tip_margin_mm))))
    for h in result.clearance[:1]:
        picks.append(("задевание", int(np.argmin(np.abs(result.plan.t - h["t"])))))
    picks.append(("конец", len(result.plan.t) - 1))
    # Одна и та же поза дважды (пик момента в паузе на последней позе = «конец»)
    # — один кадр: лента показывает разные положения, а не повторы.
    seen: list[np.ndarray] = []
    out = []
    for label, i in picks:
        q = result.plan.q[i]
        if any(np.allclose(q, s_, atol=1e-6) for s_ in seen):
            continue
        seen.append(q)
        out.append((label, i))
    return out


@op("sim_run", cli_name="sim", group="симуляция", isolate=True, memory_headroom_mb=2048,
    title="Прогнать симуляцию",
    summary=("Прогнать сценарий механизма в MuJoCo: нужный момент каждого сустава против "
             "привода, отставание при пределе момента, устойчивость или нагрузка на "
             "крепление основания, задевания звеньев по траектории (точной геометрией). "
             "Картинки — лента поз и графики моментов (приходят в ответе агенту, путь — в ответе); в окне — "
             "проигрыватель."),
    exit_code=lambda r: 1 if r.get("error") or not r.get("ok", True) else 0)
async def sim_run(
    project: Project,
    mechanism: Annotated[str, Arg(help="имя механизма (@mechanism)")],
    scenario: Annotated[str, Arg(help="имя сценария механизма")],
    geometry: Annotated[bool, Arg(help="проверять задевания звеньев по траектории "
                                       "(точная геометрия, дольше)")] = True,
    stress: Annotated[str, Arg(help="МКЭ печатных звеньев в худший момент: пусто — нет, "
                                    "all — все звенья на шарнирах, или имена звеньев через "
                                    "запятую")] = "",
    edge: Annotated[float, Arg(help="ребро сетки МКЭ, доля диагонали звена; 0 — по "
                                    "толщине тела (0.8 характерной толщины)")] = 0.0,
) -> dict[str, Any]:
    def run():
        from datum.checks import sim as S
        from datum.geom.booleans import compose
        from datum.io.artifacts import publish_glb
        from datum.sim.pictures import LINK_COLOURS, filmstrip, torque_sheet
        from datum.sim.run import poses_at

        from datum.sim.mechanism import MechanismError
        from datum.sim.stress import check_edge

        try:
            check_edge(edge)
        except MechanismError as e:
            return _err(str(e))
        pr = open_project(project)
        comp, r = pr.simulate(mechanism, scenario, geometry=geometry)
        sim = (comp, r)
        who = f"{mechanism} «{scenario}»"
        checks = (S.sim_drive(sim, name=who) + S.sim_tracking(sim, name=who)
                  + S.sim_base(sim, name=who) + S.sim_loads(sim, name=who)
                  + [S.sim_backlash(sim, name=who)]
                  + ([S.sim_clearance(sim, name=who)] if geometry else []))
        stress_out, stress_play = _stress(pr, comp, r, stress, edge, who) if stress.strip() else ([], [])
        checks += [c for c, _ in stress_out]
        lines = [c.line() for c in checks]
        lines += [f"  ({n})" for n in r.notes]

        picks = _key_instants(r)
        mats = poses_at(comp, r.plan, [i for _, i in picks])
        strip = filmstrip(comp, [(f"{label}, {r.plan.t[i]:.2f} с", m)
                                 for (label, i), m in zip(picks, mats)], title=who)
        d1 = pr.cache.put_blob(strip, "png", kind="sim", meta={"mechanism": mechanism})
        chart = torque_sheet(comp, r, title=f"{who}: моменты суставов")
        d2 = pr.cache.put_blob(chart, "png", kind="sim", meta={"mechanism": mechanism})

        # Проигрыватель окна: сетки звеньев в нулевой позе + положения по кадрам.
        links = []
        for k, (name, lk) in enumerate(comp.links.items()):
            colour = LINK_COLOURS[k % len(LINK_COLOURS)]
            view = publish_glb(pr.cache, compose(lk.solids), name=name, color=colour)
            links.append({"name": name, "url": view["url"], "color": colour})
        step = max(1, len(r.plan.t) // 600)
        # Кадры — первыми: у них свой ключ `links` (имена), а окну нужны звенья
        # с адресами сеток; наоборот имена затирали звенья, и окно (верно)
        # отказывало записи «не той формы».
        playback = {
            **r.frames, "mechanism": mechanism, "scenario": scenario, "links": links,
            "series": {"t": [round(float(v), 4) for v in r.plan.t[::step]],
                       "required": [[round(float(v), 5) for v in r.required[::step, k]]
                                    for k in range(len(r.plan.drivers))],
                       "applied": None if r.applied is None else
                       [[round(float(v), 5) for v in r.applied[::step, k]]
                        for k in range(len(r.plan.drivers))],
                       "limits": [None if comp.mechanism.links[j].joint.drive is None
                                  else comp.mechanism.links[j].joint.drive.limit
                                  for j in r.plan.drivers],
                       "units": [comp.mechanism.links[j].joint.effort_unit for j in r.plan.drivers],
                       "joints": list(r.plan.drivers)},
            "verdict": [{"id": c.id, "status": c.status, "message": c.message} for c in checks],
            # Карта загрузки: доля предела каждого звена в кадрах проигрывателя
            # (0 — свободно, 1 — на пределе привода или опоры).
            "stress": [{k: v for k, v in sp.items() if k != "path"} for sp in stress_play],
            "load": {"links": list(comp.links),
                     "u": [[round(float(np.interp(tf, r.plan.t, r.utilization[n])), 4)
                            for n in comp.links] for tf in r.frames["t"]]},
        }
        import json

        d3 = pr.cache.put_blob(json.dumps(playback, ensure_ascii=False).encode("utf-8"), "json",
                               kind="sim", meta={"mechanism": mechanism, "scenario": scenario})
        ok = all(c.status in ("pass", "skip", "warn") for c in checks)
        lines += [f"лента поз: {_blob_rel(pr, d1, 'png')}", f"моменты: {_blob_rel(pr, d2, 'png')}"]
        lines += [f"напряжения {sp['link']}: {sp['path']}" for sp in stress_play]
        return {
            "ok": ok, "mechanism": mechanism, "scenario": scenario,
            "duration_s": round(float(r.plan.t[-1]), 3),
            "mass_g": round(comp.total_mass_kg * 1000, 2),
            "joints": [{"name": j, "peak_required": round(float(np.abs(r.required[:, k]).max()), 5),
                        "limit": None if comp.mechanism.links[j].joint.drive is None
                        else comp.mechanism.links[j].joint.drive.limit,
                        "unit": comp.mechanism.links[j].joint.effort_unit,
                        "max_lag": None if r.tracking is None
                        else round(float(np.abs(r.tracking[:, k]).max()), 4)}
                       for k, j in enumerate(r.plan.drivers)],
            "base": ({"min_margin_mm": round(float(np.min(r.tip_margin_mm)), 3),
                      "max_slip": round(float(np.max(r.slip_ratio)), 4)}
                     if r.tip_margin_mm is not None else
                     {"max_force_n": round(float(np.linalg.norm(r.base_force, axis=1).max()), 3),
                      "max_moment_nm": round(float(np.linalg.norm(r.base_moment, axis=1).max()), 4)}),
            "clearance": r.clearance, "checks": [c.as_dict() for c in checks],
            "utilization_peak": {n: round(float(np.max(u)), 4) for n, u in r.utilization.items()},
            "backlash": r.backlash,
            "stress": [rep for _, rep in stress_out if rep is not None],
            "images": {"filmstrip": {"url": f"/obj/{d1}.png", "path": _blob_rel(pr, d1, "png")},
                       "torques": {"url": f"/obj/{d2}.png", "path": _blob_rel(pr, d2, "png")}},
            "playback": {"url": f"/obj/{d3}.json"},
            "events": [{"t": "sim.result", "url": f"/obj/{d3}.json", "mechanism": mechanism,
                        "scenario": scenario, "ok": ok}],
            "report": "\n".join(lines)}

    return _guard(run)


def _stress(pr, comp, r, which: str, edge: float, who: str):
    """МКЭ звеньев в худший момент: проверки, отчёты и записи для окна.

    Модуль и пределы — из материала проекта (MAT_E, MAT_TENSILE_Z/XY), как у
    каталожных проверок: свой замер параметром перебивает паспорт."""
    from datum.checks import sim as S
    from datum.io.glb import mesh_to_glb
    from datum.sim.pictures import stress_field_colours, stress_sheet
    from datum.sim.stress import link_stress, mesh_links, stress_candidates
    from datum.sim.mechanism import MechanismError

    values = pr.params.resolve()
    modulus = values.get("MAT_E")
    sz, sxy = values.get("MAT_TENSILE_Z"), values.get("MAT_TENSILE_XY")
    names = (stress_candidates(comp) if which.strip() == "all"
             else [n.strip() for n in which.split(",") if n.strip()])
    out, play = [], []
    if not names:
        from datum.sim.stress import why_no_candidates

        return [(S.CheckResult(id="sim.напряжения", title="Напряжения в звене", group="sim",
                               status="skip", severity="info",
                               message=f"{who}: {why_no_candidates(comp)}"), None)], []
    if modulus is None:
        res = S.sim_stress((comp, r), name=who)        # «модуля нет» — словами
        return [(c, None) for c in res], []
    # Сетки всех звеньев — одной очередью на все ядра: мешер — три четверти времени.
    # Не вышло у какого-то — он считается сам и назовёт причину своим отказом.
    try:
        prepared = mesh_links(comp, names, edge=edge)
    except Exception:                                        # noqa: BLE001
        prepared = {}
    for name in names:
        try:
            rep = link_stress(comp, r, name, modulus=float(modulus),
                              strength_z=None if sz is None else float(sz),
                              strength_xy=None if sxy is None else float(sxy), edge=edge,
                              prepared=prepared.get(name))
        except MechanismError as e:
            out.append((S.CheckResult(id=f"sim.напряжения.{name}", title="Напряжения в звене",
                                      group="sim", status="error", message=f"{who}: {name} — {e}"),
                        None))
            continue
        check = S.sim_stress_verdict(rep, who=who, strength_z=sz, strength_xy=sxy)
        png = stress_sheet(rep, title=f"{who}: {name}, напряжения в t = {rep.t:.2f} с")
        dp = pr.cache.put_blob(png, "png", kind="sim", meta={"link": name})
        glb = mesh_to_glb(rep.verts, rep.faces, name=f"{name} напряжения",
                          vertex_colors=stress_field_colours(rep.node_vm))
        dg = pr.cache.put_blob(glb, "glb", kind="sim", meta={"link": name})
        d = rep.as_dict()
        d["image"] = {"url": f"/obj/{dp}.png", "path": _blob_rel(pr, dp, "png")}
        out.append((check, d))
        play.append({"link": name, "t": round(rep.t, 4), "url": f"/obj/{dg}.glb",
                     "value_mpa": round(rep.value_mpa, 3), "basis": rep.basis,
                     "peak_mpa": round(rep.peak_mpa, 3),
                     "strength_mpa": None if sz is None else float(sz),
                     "u": None if rep.utilization is None else round(rep.utilization, 4),
                     "path": _blob_rel(pr, dp, "png")})
    return out, play


@op("sim_stress", cli_name="sim-stress", group="симуляция", isolate=True, memory_headroom_mb=4096,
    title="Напряжения звеньев в худший момент",
    summary=("МКЭ печатных звеньев механизма в момент наибольшего изгиба у оси сустава: "
             "опора — касание с родителем, нагрузки — из динамики сценария. Мизес против "
             "предела между слоями, картинка поля на каждое звено (в ответе агенту); в "
             "окне — поле на звене кнопкой проигрывателя. Минуты на звено: сетки строятся "
             "на всех ядрах сразу."),
    exit_code=lambda r: 1 if r.get("error") or not r.get("ok", True) else 0)
async def sim_stress(
    project: Project,
    mechanism: Annotated[str, Arg(help="имя механизма (@mechanism)")],
    scenario: Annotated[str, Arg(help="имя сценария механизма")],
    links: Annotated[str, Arg(help="all — все звенья на шарнирах, или имена через запятую")] = "all",
    edge: Annotated[float, Arg(help="ребро сетки МКЭ, доля диагонали звена; 0 — по "
                                    "толщине тела")] = 0.0,
) -> dict[str, Any]:
    def run():
        from datum.sim.mechanism import MechanismError
        from datum.sim.stress import check_edge

        try:
            check_edge(edge)
        except MechanismError as e:
            return _err(str(e))
        pr = open_project(project)
        comp, r = pr.simulate(mechanism, scenario, geometry=False)
        who = f"{mechanism} «{scenario}»"
        out, play = _stress(pr, comp, r, links or "all", edge, who)
        checks = [c for c, _ in out]
        ok = all(c.status in ("pass", "skip", "warn") for c in checks)
        lines = [c.line() for c in checks] + [f"напряжения {sp['link']}: {sp['path']}" for sp in play]
        return {"ok": ok, "mechanism": mechanism, "scenario": scenario,
                "checks": [c.as_dict() for c in checks],
                "stress": [rep for _, rep in out if rep is not None],
                # для окна: те же записи, что `stress` в записи проигрывателя
                "playback_stress": [{k: v for k, v in sp.items() if k != "path"} for sp in play],
                "report": "\n".join(lines)}

    return _guard(run)


@op("part_mass", cli_name="mass", group="симуляция", isolate=True,
    title="Масса деталей по настройкам печати",
    summary=("Масса, центр масс и доля сплошного у печатных деталей — по их НАСТРОЙКАМ "
             "ПЕЧАТИ (стенки, заполнение, верх/низ; профиль слайсера ← [print] ← "
             "@part(print=…)), как их положит слайсер (сверено с G-code до ±4 %). И какие "
             "правки уйдут в слайсер при slice."))
async def part_mass(
    project: Project,
    parts: Annotated[list[str] | None, Arg(help="имена деталей; пусто — все печатные",
                                           metavar="ДЕТАЛЬ")] = None,
) -> dict[str, Any]:
    def run():
        from datum.fab.mass import cached_print_mass

        pr = open_project(project)
        names = list(parts) if parts else sorted(n for n, d in pr.parts.items() if d.printed)
        built = pr.build(names)
        broken = [r.line() for r in built.values() if not r.ok]
        if broken:
            return _err("не собрались детали:\n" + "\n".join(broken))
        out, lines, total = {}, [], 0.0
        for name in names:
            s = pr.print_settings(name)
            props = cached_print_mass(built[name].solid, s, up=pr.parts[name].print_up,
                                      cache_dir=pr.root / ".datum" / "mass")
            total += props.mass_g
            over = s.slicer_overrides()
            out[name] = {**props.as_dict(), "slicer_overrides": over}
            lines.append(f"{name}: {props.mass_g:.1f} г ({s.describe()}; сплошного "
                         f"{props.shell_fraction * 100:.0f} %)"
                         + (f" → слайсеру: {', '.join(f'{k}={v}' for k, v in over.items())}"
                            if over else ""))
        src = next(iter(out.values()))["settings"]["sources"].get("flow", "") if out else ""
        lines.append(f"итого {total:.1f} г; подача и ширины линий — {src}")
        return {"parts": out, "total_g": round(total, 3), "report": "\n".join(lines)}

    return _guard(run)
