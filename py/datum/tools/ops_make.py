"""Пробный образец посадки и ведомость — то, что агент делал руками.

`coupon` — «какой файл печатать первым для тестов?» (владелец, Gear Cube):
агент сам вырезал из деталей узел сопряжения (`тест_посадки`), чтобы зазор
проверили за десять минут печати, а не за десять часов. Теперь это операция:
коробка вокруг места посадки, каждая деталь режется ею и выгружается в своей
позе печати.

`bom` — ведомость изделия: что печатать (масса по настройкам печати), что
покупать (детали с `printed=False`), сколько чего. Закупку агент вёл руками в
`ЗАКУПКА.md`; число печатных деталей и их масса при этом расходились с
проектом после каждой правки.
"""

from __future__ import annotations

from typing import Annotated, Any

from datum.tools.ops import Project, _err, _guard, open_project
from datum.tools.registry import Arg, op

__all__ = ["coupon", "bom"]

#: Образец тоньше этого по любой оси — не образец, а стружка.
COUPON_MIN_MM = 2.0


@op("coupon", cli_name="coupon", group="сборка", isolate=True, danger="mutates",
    title="Пробный образец посадки",
    summary=("Вырезать из деталей узел сопряжения коробкой box (x0 y0 z0 x1 y1 z1, мм, в "
             "координатах сборки) и выгрузить каждый кусок в output/coupons/ в позе "
             "печати своей детали — чтобы проверить зазор или защёлку за минуты печати, "
             "а не за часы. Срез открыт: стенка образца — это сечение детали. Размеры "
             "посадки в образце те же, что в детали; жёсткость — нет."))
async def coupon(
    project: Project,
    parts: Annotated[list[str], Arg(help="детали, из которых вырезать (обычно две "
                                         "сопрягаемые)", metavar="ДЕТАЛЬ")],
    box: Annotated[list[float], Arg(help="коробка выреза: x0,y0,z0,x1,y1,z1, мм",
                                    metavar="ЧИСЛО")],
    name: Annotated[str, Arg(help="имя образца (в имени файлов); пусто — «образец»")] = "",
) -> dict[str, Any]:
    def run():
        from datum.geom import box as make_box
        from datum.geom.measure import bbox, vol
        from datum.io.export import on_bed, safe_name, write_3mf

        if len(box) != 6:
            return _err(f"box — шесть чисел x0 y0 z0 x1 y1 z1, получено {len(box)}")
        x0, y0, z0, x1, y1, z1 = (float(v) for v in box)
        lo = (min(x0, x1), min(y0, y1), min(z0, z1))
        hi = (max(x0, x1), max(y0, y1), max(z0, z1))
        if any(h - l < COUPON_MIN_MM for l, h in zip(lo, hi)):
            return _err(f"коробка {[round(h - l, 2) for l, h in zip(lo, hi)]} мм: образец "
                        f"тоньше {COUPON_MIN_MM:g} мм не напечатать и в руки не взять")
        if not parts:
            return _err("не названо ни одной детали")
        pr = open_project(project)
        unknown = [n for n in parts if n not in pr.parts]
        if unknown:
            return _err(f"нет таких деталей: {unknown}; есть: {sorted(pr.parts)}")
        built = pr.build(list(parts))
        broken = [r.line() for r in built.values() if not r.ok]
        if broken:
            return _err("не собрались детали:\n" + "\n".join(broken))
        cutter = make_box(lo[0], hi[0], lo[1], hi[1], lo[2], hi[2])
        label = safe_name(name.strip() or "образец")
        out_dir = pr.root / "output" / "coupons"
        lines = [f"образец «{label}»: коробка X {lo[0]:g}…{hi[0]:g}, Y {lo[1]:g}…{hi[1]:g}, "
                 f"Z {lo[2]:g}…{hi[2]:g} мм"]
        done: dict[str, Any] = {}
        for part_name, r in built.items():
            piece = r.solid ^ cutter                 # `^` у Solid — пересечение
            volume = float(vol(piece))
            if volume <= 1e-6:
                p_lo, p_hi = bbox(r.solid)
                return _err(f"коробка не задевает деталь {part_name}: её габарит X "
                            f"{p_lo[0]:.1f}…{p_hi[0]:.1f}, Y {p_lo[1]:.1f}…{p_hi[1]:.1f}, Z "
                            f"{p_lo[2]:.1f}…{p_hi[2]:.1f} мм. Коробка задаётся в координатах, "
                            f"в которых детали стоят (scene_view покажет)")
            share = volume / max(float(vol(r.solid)), 1e-9)
            up = pr.parts[part_name].print_up
            placed = on_bed(piece, up, pr.bed)
            written = write_3mf(out_dir / f"{label}-{safe_name(part_name)}.3mf", placed,
                                title=f"{label}: {part_name}",
                                description=f"Пробный образец посадки «{label}» из детали "
                                            f"{part_name}: вырез, не деталь")
            rel = str(written.path.relative_to(pr.root))
            p_lo, p_hi = bbox(placed)
            size = [round(float(h - l), 2) for l, h in zip(p_lo, p_hi)]
            lines.append(f"  {part_name} → {rel}: {size[0]}×{size[1]}×{size[2]} мм на столе, "
                         f"{volume:.0f} мм³ ({share * 100:.0f} % детали)"
                         + (f", вверх {up}" if up else ", поза не объявлена — как построено"))
            done[part_name] = {"file": rel, "volume_mm3": round(volume, 3),
                               "share": round(share, 4), "extents_mm": size, "print_up": up}
        lines.append("Срез открыт — это сечение детали. Посадка в образце та же, что в "
                     "детали; жёсткость стенок — нет (они короче): тугую защёлку образец "
                     "покажет мягче. Итог пробной печати запишите: print_log(…), "
                     "а замеренный зазор — param_set(…, source=\"пробный образец\").")
        return {"name": label, "box": [*lo, *hi], "parts": done, "report": "\n".join(lines)}

    return _guard(run)


@op("bom", cli_name="bom", group="проект", isolate=True,
    title="Ведомость изделия",
    summary=("Что печатать и что покупать: печатные детали с массой по настройкам "
             "печати и суммой пластика, покупные и показные (printed=False) — списком. "
             "Одинаковые по форме детали сведены в одну строку с количеством. Цен и "
             "ссылок Datum не знает — их ведёт человек."))
async def bom(project: Project) -> dict[str, Any]:
    def run():
        pr = open_project(project)
        if not pr.parts:
            return _err("в проекте нет деталей")
        built = pr.build()
        printed_rows: dict[str, dict] = {}
        bought: list[dict] = []
        broken: list[str] = []
        for part_name in sorted(pr.parts):
            r = built[part_name]
            pdef = pr.parts[part_name]
            if not r.ok:
                broken.append(part_name)
                continue
            if not pdef.printed:
                bought.append({"name": part_name,
                               "volume_mm3": round(r.topology.volume, 1)})
                continue
            # Одинаковые детали (левая и правая копии одной формы) — одной
            # строкой: печатают их по счёту, а не по именам.
            key = r.solid.digest()
            row = printed_rows.setdefault(key, {"names": [], "count": 0, "mass_g": None,
                                                "volume_mm3": round(r.topology.volume, 1),
                                                "print_up": pdef.print_up})
            row["names"].append(part_name)
            row["count"] += 1
        masses = _masses(pr, [row["names"][0] for row in printed_rows.values()])
        total = 0.0
        lines = [f"ПЕЧАТАТЬ — деталей {sum(r['count'] for r in printed_rows.values())}, "
                 f"разных {len(printed_rows)}:"]
        rows = []
        for row in sorted(printed_rows.values(), key=lambda r: r["names"][0]):
            mass = masses.get(row["names"][0])
            row["mass_g"] = None if mass is None else round(mass, 2)
            if mass is not None:
                total += mass * row["count"]
            many = f" ×{row['count']} ({', '.join(row['names'])})" if row["count"] > 1 else ""
            lines.append(f"  {row['names'][0]}{many}: "
                         + (f"{mass:.1f} г" if mass is not None else "масса не посчитана")
                         + (f" ×{row['count']} = {mass * row['count']:.1f} г"
                            if mass is not None and row["count"] > 1 else "")
                         + (f", вверх {row['print_up']}" if row["print_up"]
                            else ", поза печати НЕ объявлена"))
            rows.append(row)
        lines.append(f"  пластика всего ≈ {total:.0f} г (по настройкам печати; без "
                     f"поддержек, каймы и прочистки)")
        lines.append("")
        if bought:
            lines.append(f"НЕ ПЕЧАТАЕТСЯ (покупное и показное, printed=False) — {len(bought)}:")
            lines += [f"  {b['name']}" for b in bought]
            lines.append("  количество, цену и где купить ведёт человек: Datum знает о них "
                         "только форму")
        else:
            lines.append("покупного в проекте не объявлено (@part(..., printed=False)): "
                         "винты, подшипники, моторы — если они есть в изделии, добавьте их "
                         "макеты, иначе ни проверки, ни эта ведомость о них не знают")
        # Составная деталь без позы печати среди печатных — почти наверняка
        # показная сборка, объявленная до появления `printed=False`: в сумму
        # пластика она вошла бы целым изделием (Gear Cube: «сборка» 347 г).
        showy = [n for row in rows for n in row["names"]
                 if pr.parts[n].expect.bodies != 1 and not pr.parts[n].print_up]
        if showy:
            lines.append("")
            lines.append(f"! похоже на показную сборку, а числится печатной: "
                         f"{', '.join(showy)} — объявите @part(..., printed=False), иначе "
                         f"она входит в сумму пластика и в выгрузку")
        if broken:
            lines.append("")
            lines.append(f"НЕ СОБРАЛИСЬ и в ведомость не вошли: {', '.join(broken)}")
        return {"printed": rows, "bought": bought, "broken": broken, "suspect": showy,
                "plastic_g": round(total, 1), "report": "\n".join(lines)}

    return _guard(run)


def _masses(pr, names: list[str]) -> dict[str, float]:
    """Масса печатных деталей по настройкам печати — той же функцией, что у
    `part_mass` и симуляции. Не посчиталась — детали в словаре нет."""
    from datum.fab.mass import cached_print_mass

    out: dict[str, float] = {}
    built = pr.build(names)
    for name in names:
        try:
            props = cached_print_mass(built[name].solid, pr.print_settings(name),
                                      up=pr.parts[name].print_up,
                                      cache_dir=pr.root / ".datum" / "mass")
            out[name] = float(props.mass_g)
        except Exception:                 # масса — справка: без неё ведомость остаётся
            continue
    return out
