"""Сравнение двух снимков.

Отвечает на вопрос «что изменилось между версиями», который в рабочем процессе
CAD вёлся руками в NOTES.md. Три слоя, от причины к следствию:

  1. параметры — что человек поменял;
  2. метрики деталей — во что это вылилось в геометрии;
  3. переходы проверок — что из-за этого починилось и что сломалось.

Третий слой и есть смысл: регрессия должна быть видна сразу, а не обнаруживаться
после печати.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from datum.doc.snapshot import PartMetrics, Snapshot

__all__ = ["ParamChange", "PartChange", "CheckTransition", "Diff", "diff"]

Transition = Literal["fixed", "broken", "still_failing", "unchanged", "new", "removed"]


@dataclass(frozen=True, slots=True)
class ParamChange:
    name: str
    kind: Literal["added", "removed", "changed", "provenance"]
    before: float | None = None
    after: float | None = None
    unit: str = ""
    prov_before: str = ""
    prov_after: str = ""

    @property
    def measured_now(self) -> bool:
        """Угаданное стало измеренным — это хорошая новость, её стоит показать."""
        return self.prov_before == "assumed" and self.prov_after == "measured"

    def _shown(self, value: float | None) -> float:
        """Значение в единице ввода.

        В снимке параметры лежат в КАНОНЕ (мм, градусы, Н, МПа, г), а единица
        при них — та, в которой их вводили. Раньше канон печатался под ярлыком
        единицы ввода: «H: 5.08 → 7.62 in» про 0.2 и 0.3 дюйма.
        """
        from datum.doc.units import from_canonical

        try:
            return from_canonical(value or 0.0, self.unit) if self.unit else (value or 0.0)
        except ValueError:
            return value or 0.0

    def line(self) -> str:
        unit = f" {self.unit}" if self.unit else ""
        if self.kind == "added":
            return f"  + {self.name} = {self._shown(self.after):g}{unit} ({self.prov_after})"
        if self.kind == "removed":
            return f"  − {self.name} (было {self._shown(self.before):g}{unit})"
        if self.kind == "provenance":
            mark = "  ✓ " if self.measured_now else "  ~ "
            return (f"{mark}{self.name}: происхождение {self.prov_before} → {self.prov_after}"
                    + (" — неизвестное стало измеренным" if self.measured_now else ""))
        before, after = self._shown(self.before), self._shown(self.after)
        return (f"  ~ {self.name}: {before:g} → {after:g}{unit} "
                f"({after - before:+.4g})")


@dataclass(frozen=True, slots=True)
class PartChange:
    name: str
    kind: Literal["added", "removed", "changed", "unchanged"]
    before: PartMetrics | None = None
    after: PartMetrics | None = None

    @property
    def dv(self) -> float:
        if not (self.before and self.after):
            return 0.0
        return self.after.volume_mm3 - self.before.volume_mm3

    @property
    def dv_frac(self) -> float:
        if not (self.before and self.after) or self.before.volume_mm3 <= 0:
            return 0.0
        return self.dv / self.before.volume_mm3

    def line(self) -> str:
        if self.kind == "added":
            return f"  + {self.name}: {self.after.volume_mm3:.1f} мм³"
        if self.kind == "removed":
            return f"  − {self.name} (было {self.before.volume_mm3:.1f} мм³)"
        if self.kind == "unchanged":
            return f"  = {self.name}: не изменилась"
        a, b = self.before, self.after
        bits = [f"объём {a.volume_mm3:.1f} → {b.volume_mm3:.1f} мм³ "
                f"({self.dv:+.1f}, {100 * self.dv_frac:+.1f} %)"]
        ea, eb = a.extents, b.extents
        if any(abs(eb[i] - ea[i]) > 1e-4 for i in range(3)):
            bits.append(f"габарит {ea[0]:.1f}×{ea[1]:.1f}×{ea[2]:.1f} → "
                        f"{eb[0]:.1f}×{eb[1]:.1f}×{eb[2]:.1f}")
        if a.bodies != b.bodies:
            bits.append(f"тел {a.bodies} → {b.bodies}")
        if a.sealed_voids != b.sealed_voids:
            bits.append(f"полостей {a.sealed_voids} → {b.sealed_voids}")
        if a.genus != b.genus:
            bits.append(f"род {a.genus} → {b.genus}")
        if a.ok != b.ok:
            bits.append("СБОЙ СБОРКИ" if not b.ok else "сборка починилась")
        # Код проекта — тело функции, файл детали и lib/. У снимков старше
        # поля `code_digest` его нет, и для них судит только тело функции.
        code_changed = a.source_digest != b.source_digest or (
            a.code_digest and b.code_digest and a.code_digest != b.code_digest)
        if code_changed:
            bits.append("изменён код детали")
        return f"  ~ {self.name}: " + "; ".join(bits)


@dataclass(frozen=True, slots=True)
class CheckTransition:
    id: str
    transition: Transition
    before_status: str = ""
    after_status: str = ""
    before_value: float | None = None
    after_value: float | None = None
    unit: str = ""
    message: str = ""
    before_message: str = ""

    def reason(self) -> str:
        """Чем дело было плохо. Величина этого не показывает.

        У проверки `escape`, например, отказ бывает двух родов — «не держит» и
        «клинит», — но измеренная величина в обоих случаях одна и та же (объём
        упора). Без причины строка «20.7 → 8.65» читается как ухудшение, хотя на
        деле это переход от заклинившей детали к нормально сидящей.
        """
        src = (self.before_message if self.transition in ("fixed", "still_failing")
               else self.message)
        if not src:
            return ""
        # Сообщение выглядит как «шторка +X: КЛИНИТ — при сдвиге …». Отрезаем
        # только имя детали с направлением: вердикт («КЛИНИТ», «НЕ ДЕРЖИТ») —
        # самое ценное в строке, терять его нельзя.
        text = src.split(": ", 1)[1].strip() if ": " in src else src.strip()
        limit = 100
        return text[:limit] + ("…" if len(text) > limit else "")

    def line(self) -> str:
        mark = {"fixed": "  ✓ ", "broken": "  ✗ ", "still_failing": "  · ",
                "new": "  + ", "removed": "  − ", "unchanged": "  = "}[self.transition]
        word = {"fixed": "ПОЧИНИЛОСЬ", "broken": "СЛОМАЛОСЬ",
                "still_failing": "всё ещё падает", "new": "новая",
                "removed": "убрана", "unchanged": ""}[self.transition]
        # Безразмерные величины помечены «—»; печатать это в конце строки незачем.
        unit = "" if self.unit in ("", "—", "-", "доля") else f" {self.unit}"
        vals = ""
        if self.before_value is not None and self.after_value is not None:
            vals = f": {self.before_value:.3g} → {self.after_value:.3g}{unit}"
        elif self.after_value is not None:
            vals = f": {self.after_value:.3g}{unit}"
        head = f"{mark}{self.id} {word}{vals}".rstrip()
        why = self.reason() if self.transition in ("fixed", "broken", "still_failing") else ""
        return head + (f"\n        было: {why}" if why and self.transition == "fixed"
                       else f"\n        стало: {why}" if why else "")


@dataclass(frozen=True, slots=True)
class Diff:
    a: Snapshot
    b: Snapshot
    params: tuple[ParamChange, ...] = ()
    parts: tuple[PartChange, ...] = ()
    checks: tuple[CheckTransition, ...] = ()

    # ---------------------------------------------------------------- выборки
    def transitions(self, *kinds: Transition) -> tuple[CheckTransition, ...]:
        return tuple(c for c in self.checks if c.transition in kinds)

    @property
    def broken(self) -> tuple[CheckTransition, ...]:
        return self.transitions("broken")

    @property
    def fixed(self) -> tuple[CheckTransition, ...]:
        return self.transitions("fixed")

    @property
    def is_regression(self) -> bool:
        return bool(self.broken)

    @property
    def changed_parts(self) -> tuple[PartChange, ...]:
        return tuple(p for p in self.parts if p.kind != "unchanged")

    # ---------------------------------------------------------------- вывод
    def report(self) -> str:
        lines = [f"{self.a.tag} → {self.b.tag}"]

        changed_params = [p for p in self.params]
        lines.append("")
        lines.append("Параметры:" if changed_params else "Параметры: без изменений")
        lines += [p.line() for p in changed_params]

        lines.append("")
        changed = self.changed_parts
        if changed:
            lines.append("Детали:")
            lines += [p.line() for p in changed]
            same = len(self.parts) - len(changed)
            if same:
                lines.append(f"  (без изменений: {same})")
        else:
            lines.append("Детали: без изменений")

        lines.append("")
        interesting = self.transitions("fixed", "broken", "still_failing", "new", "removed")
        if interesting:
            lines.append("Проверки:")
            lines += [c.line() for c in interesting]
        else:
            lines.append("Проверки: без изменений")

        lines.append("")
        if self.is_regression:
            lines.append(f"РЕГРЕССИЯ: сломалось проверок — {len(self.broken)}")
        elif self.fixed:
            lines.append(f"Починилось проверок: {len(self.fixed)}")
        else:
            lines.append("Регрессий нет.")
        dm = self.b.total_mass_g - self.a.total_mass_g
        if self.a.mass_by_print != self.b.mass_by_print:
            # Старый снимок считал объём × плотность, новый — настройки печати:
            # разница в полтора-два раза была бы про способ счёта, а не про деталь.
            def how(s):
                return "по настройкам печати" if s.mass_by_print else "сплошным телом"
            lines.append(f"Масса: {self.a.total_mass_g:.1f} г ({how(self.a)}) и "
                         f"{self.b.total_mass_g:.1f} г ({how(self.b)}) — считаны по-разному, "
                         f"сравнивать нельзя")
        elif abs(dm) > 1e-6:
            lines.append(f"Масса: {self.a.total_mass_g:.1f} → {self.b.total_mass_g:.1f} г "
                         f"({dm:+.1f})")
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {
            "a": self.a.tag, "b": self.b.tag,
            "regression": self.is_regression,
            # Значения — в единице параметра, как и строка отчёта: окно печатает
            # их рядом с этой единицей. Канон остаётся в *_canonical.
            "params": [{"name": p.name, "kind": p.kind,
                        "before": None if p.before is None else p._shown(p.before),
                        "after": None if p.after is None else p._shown(p.after),
                        "before_canonical": p.before, "after_canonical": p.after,
                        "unit": p.unit,
                        "prov_before": p.prov_before, "prov_after": p.prov_after}
                       for p in self.params],
            "parts": [{"name": p.name, "kind": p.kind,
                       "dv_mm3": round(p.dv, 4), "dv_frac": round(p.dv_frac, 6)}
                      for p in self.parts],
            "checks": [{"id": c.id, "transition": c.transition,
                        "before": c.before_status, "after": c.after_status,
                        "before_value": c.before_value, "after_value": c.after_value}
                       for c in self.checks],
        }


def _check_index(snap: Snapshot) -> dict[str, dict]:
    return {r["id"]: r for r in snap.checks.get("results", [])}


def diff(a: Snapshot, b: Snapshot) -> Diff:
    """Сравнить два снимка: параметры, метрики деталей, переходы проверок."""
    # ---- параметры
    pchanges: list[ParamChange] = []
    for name in sorted(set(a.params) | set(b.params)):
        ma = a.params_meta.get(name, {})
        mb = b.params_meta.get(name, {})
        unit = mb.get("unit") or ma.get("unit") or ""
        if name not in a.params:
            pchanges.append(ParamChange(name, "added", after=b.params[name], unit=unit,
                                        prov_after=mb.get("provenance", "")))
        elif name not in b.params:
            pchanges.append(ParamChange(name, "removed", before=a.params[name], unit=unit,
                                        prov_before=ma.get("provenance", "")))
        else:
            va, vb = a.params[name], b.params[name]
            pa, pb = ma.get("provenance", ""), mb.get("provenance", "")
            if abs(va - vb) > 1e-9:
                pchanges.append(ParamChange(name, "changed", before=va, after=vb,
                                            unit=unit, prov_before=pa, prov_after=pb))
            elif pa != pb:
                pchanges.append(ParamChange(name, "provenance", before=va, after=vb,
                                            unit=unit, prov_before=pa, prov_after=pb))

    # ---- детали
    partchanges: list[PartChange] = []
    for name in sorted(set(a.parts) | set(b.parts)):
        pa_, pb_ = a.parts.get(name), b.parts.get(name)
        if pa_ is None:
            partchanges.append(PartChange(name, "added", after=pb_))
        elif pb_ is None:
            partchanges.append(PartChange(name, "removed", before=pa_))
        elif (pa_.result_digest and pa_.result_digest == pb_.result_digest
              and pa_.ok == pb_.ok):
            # Совпал отпечаток геометрии — деталь буквально та же.
            partchanges.append(PartChange(name, "unchanged", before=pa_, after=pb_))
        else:
            partchanges.append(PartChange(name, "changed", before=pa_, after=pb_))

    # ---- проверки
    ia, ib = _check_index(a), _check_index(b)
    trans: list[CheckTransition] = []
    for cid in sorted(set(ia) | set(ib)):
        ra, rb = ia.get(cid), ib.get(cid)
        if ra is None:
            trans.append(CheckTransition(cid, "new", after_status=rb["status"],
                                         after_value=rb.get("value"),
                                         unit=rb.get("unit", ""),
                                         message=rb.get("message", "")))
            continue
        if rb is None:
            trans.append(CheckTransition(cid, "removed", before_status=ra["status"],
                                         before_value=ra.get("value"),
                                         unit=ra.get("unit", "")))
            continue
        ok_a = ra["status"] in ("pass", "skip")
        ok_b = rb["status"] in ("pass", "skip")
        if ok_a and not ok_b:
            kind = "broken"
        elif not ok_a and ok_b:
            kind = "fixed"
        elif not ok_a and not ok_b:
            kind = "still_failing"
        else:
            kind = "unchanged"
        trans.append(CheckTransition(cid, kind, before_status=ra["status"],
                                     after_status=rb["status"],
                                     before_value=ra.get("value"),
                                     after_value=rb.get("value"),
                                     unit=rb.get("unit", ""),
                                     message=rb.get("message", ""),
                                     before_message=ra.get("message", "")))

    return Diff(a=a, b=b, params=tuple(pchanges), parts=tuple(partchanges),
                checks=tuple(trans))
