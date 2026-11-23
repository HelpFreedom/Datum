"""Отчёт нарезки: что слайсер сделал с деталью, рядом с проверками Datum.

`slice` режет и сверяет подвал G-code; тело G-code разбирает
`io/gcode_report.py`. Здесь — общая часть для `slice` и `slice_report`:
строки отчёта, сверка с проверками печатности Datum и названные расхождения.

Расхождение — не ошибка одной из сторон, а место, куда надо посмотреть:
Datum судит по геометрии (грань круче 45°, полоса над пустотой), слайсер — по
тому, что вышло после нарезки на линии. Когда Datum говорит «нависаний нет», а
слайсер печатает стенки как нависающие, — прав слайсер: печатать будет он.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from datum.tools.ops import Project, _err, _guard, _printed_names, _skipped_line, open_project
from datum.tools.registry import Arg, op

__all__ = ["slice_report", "gcode_findings"]

#: Меньше этого (мм линий слайсера) — не повод говорить о расхождении: один
#: короткий отрезок у кромки слайсер помечает нависающим и на ровной детали.
MISMATCH_MIN_MM = 5.0
#: Меньше этого (мм² граней Datum) — «нависаний нет».
DATUM_NONE_MM2 = 1.0


def gcode_findings(solid, up: str | None, gcode: Path, config: dict,
                   name: str) -> tuple[list[str], list[str], dict]:
    """(строки отчёта, предупреждения, поля) по телу G-code одной детали.

    Строки — без отступа. Предупреждения — то, что нельзя пропустить: остров
    в воздухе при выключенных поддержках. Проверки Datum считаются здесь же,
    на том же теле и в той же позе, что ушли слайсеру.
    """
    from datum.checks.dfam import DEFAULT_STEP, bridge_span
    from datum.checks.printing import overhang
    from datum.geom.measure import extents
    from datum.io.gcode_report import analyse

    density = float(config.get("filament_density", 1.24) or 1.24)
    diameter = float(str(config.get("filament_diameter", 1.75)).split(",")[0] or 1.75)
    report = analyse(gcode, density=density, diameter=diameter)
    supports_on = str(config.get("enable_support", "")).strip() == "1"
    lines = report.lines(supports_on=supports_on)
    warnings: list[str] = []
    if report.islands and not supports_on:
        worst = max(report.islands, key=lambda i: i.area_mm2)
        warnings.append(
            f"островов в воздухе {len(report.islands)} при выключенных поддержках — "
            f"крупнейший {worst.area_mm2:.1f} мм² в слое {worst.layer} (Z {worst.z:.2f}) у "
            f"X {worst.centre[0]:.1f} Y {worst.centre[1]:.1f}: этот пластик ляжет в пустоту")
    if supports_on:
        lines.append(f"поддержки: {report.support_g:.2f} г пластика"
                     + ("" if report.support_g > 0.005 else " — слайсер их не поставил: "
                        "по его правилу опирать нечего"))

    pose = up or "+Z"
    over = overhang(solid, up=pose, name=name)
    over_mm2 = float(over.evidence[0].data["area_mm2"]) if over.evidence else 0.0
    step = max(DEFAULT_STEP, max(extents(solid)) / 400)
    span = float(bridge_span(solid, up=pose, step=step, name=name).value or 0.0)
    hanging = report.overhang_mm + report.bridge_mm
    lines.append(f"сверка с Datum: нависаний круче 45° — {over_mm2:.0f} мм² граней (слайсер: "
                 f"{report.overhang_mm:.0f} мм нависающих стенок, {report.bridge_mm:.0f} мм "
                 f"мостов); самый длинный мост — Datum {span:.1f} мм, слайсер "
                 f"{report.longest_bridge_mm:.1f} мм")
    mismatches: list[str] = []
    if over_mm2 < DATUM_NONE_MM2 and hanging >= MISMATCH_MIN_MM:
        mismatches.append(
            f"Datum нависаний не видит, а слайсер печатает {hanging:.0f} мм линий как "
            f"нависающие и мосты — смотрите слои выше (section по высоте, print-карта)")
    if span < 1.0 and report.longest_bridge_mm >= MISMATCH_MIN_MM:
        mismatches.append(
            f"Datum мостом это не считает (у него мост — полоса, опёртая с обоих концов "
            f"и дальше 1 мм от стены своего слоя; узкая кромка и консоль — нависание), "
            f"слайсер тянет мост {report.longest_bridge_mm:.1f} мм на Z "
            f"{report.longest_bridge_z:.2f}")
    if over_mm2 >= 20.0 and hanging < MISMATCH_MIN_MM and report.support_g <= 0.005 \
            and not report.islands:
        lines.append("  эти грани слайсер напечатал обычными стенками, нависающими не "
                     "пометил и поддержек под ними нет — каким выйдет низ, покажет печать")
    lines += [f"РАСХОЖДЕНИЕ: {m}" for m in mismatches]
    fields = report.as_dict()
    fields["datum"] = {"overhang_mm2": round(over_mm2, 1), "longest_bridge_mm": round(span, 2)}
    fields["mismatches"] = mismatches
    return lines, warnings, fields


def _newest_source(root: Path) -> tuple[float, str]:
    """Самая свежая правка того, из чего строится деталь: (время, файл)."""
    best, who = 0.0, ""
    for pattern in ("datum.toml", "placement.yaml", "params/*.yaml", "parts/*.py",
                    "lib/**/*.py", "imported/*", "data/**/*"):
        for p in root.glob(pattern):
            try:
                if p.is_file() and p.stat().st_mtime > best:
                    best, who = p.stat().st_mtime, str(p.relative_to(root))
            except OSError:
                continue
    return best, who


@op("slice_report", cli_name="slice-report", group="сборка", isolate=True,
    title="Отчёт нарезки по G-code",
    summary=("Разобрать уже нарезанный G-code деталей (output/, от slice) по слоям и "
             "сверить с проверками Datum: острова в воздухе (кусок слоя, под которым нет "
             "ни детали, ни поддержки), стенки, которые слайсер пометил нависающими, "
             "мосты с длинами, площадь первого слоя, пластик по видам линий. Ничего не "
             "режет и не пишет; свежую нарезку даёт slice — её отчёт несёт то же самое."),
    exit_code=lambda r: 1 if r.get("error") else 0)
async def slice_report(
    project: Project,
    parts: Annotated[list[str] | None, Arg(help="имена деталей; пусто — все, у которых "
                                                "есть G-code", metavar="ДЕТАЛЬ")] = None,
) -> dict[str, Any]:
    def run():
        from datum.io.export import safe_name
        from datum.io.slicer import SlicerError, parse_gcode

        pr = open_project(project)
        unknown = [n for n in (parts or []) if n not in pr.parts]
        if unknown:
            return _err(f"нет таких деталей: {unknown}; есть: {sorted(pr.parts)}")
        names, skipped = _printed_names(pr, parts)
        out_dir = pr.root / "output"
        material = safe_name(str(pr.manifest.get("printer", {}).get("material", "PLA")))
        found: dict[str, Path] = {}
        missing: list[str] = []
        for name in names:
            exact = out_dir / f"{safe_name(name)}_{material}.gcode"
            # Пластик могли сменить после нарезки: берётся самый свежий файл
            # детали, а его пластик называет подвал.
            others = sorted(out_dir.glob(f"{safe_name(name)}_*.gcode"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
            path = exact if exact.is_file() else (others[0] if others else None)
            if path is None:
                missing.append(name)
            else:
                found[name] = path
        if not found:
            return _err("нарезанного G-code в output/ нет"
                        + (f" для: {', '.join(missing)}" if parts else "")
                        + " — сначала slice")
        built = pr.build(list(found))
        broken = [r.line() for r in built.values() if not r.ok]
        if broken:
            return _err("не собрались детали — сверять не с чем:\n" + "\n".join(broken))
        edited, edited_file = _newest_source(pr.root)
        lines: list[str] = []
        result: dict[str, Any] = {}
        stale: list[str] = []
        for name, path in found.items():
            rel = str(path.relative_to(pr.root))
            try:
                config = parse_gcode(path)
                part_lines, warnings, fields = gcode_findings(
                    built[name].solid, pr.parts[name].print_up, path, config, name)
            except (SlicerError, OSError, ValueError) as e:
                lines.append(f"{name} ← {rel}: не разобрать ({e})")
                result[name] = {"file": rel, "error": str(e)}
                continue
            lines.append(f"{name} ← {rel}: слоёв {fields['layers']}, высота "
                         f"{fields['height_mm']:.2f} мм, "
                         f"{config.get('estimated printing time (normal mode)', 'время ?')}, "
                         f"{config.get('filament used [g]', '?')} г")
            if path.stat().st_mtime < edited:
                stale.append(name)
                lines.append(f"  ! нарезано ДО последней правки проекта ({edited_file}): "
                             f"числа слайсера — про прежнюю нарезку, а сверка с Datum — "
                             f"про нынешнюю деталь. Свежее даст slice")
            lines += [f"  {line}" for line in part_lines]
            lines += [f"  ! {w}" for w in warnings]
            fields.update(file=rel, warnings=warnings, stale=name in stale)
            result[name] = fields
        if missing:
            lines.append("")
            lines.append(f"без G-code (не нарезаны): {', '.join(missing)}")
        if skipped:
            lines.append(_skipped_line(skipped))
        return {"parts": result, "missing": missing, "stale": stale, "skipped": skipped,
                "report": "\n".join(lines)}

    return _guard(run)
