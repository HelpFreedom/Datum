"""Исполнитель проверок и развёртка по незамеренному.

Претензия из ТЗ, ради которой всё это:

    «Должен быть явный блок неизвестных с диапазонами, и проверки должны
     гоняться по диапазону, показывая, какая проверка от какого неизвестного
     зависит. Тогда я бы вам в первый день сказал: померьте хвостовик,
     остальное нечувствительно.»

Как это устроено здесь.

Зависимости проверок НЕ объявляются руками — объявления разъезжаются с кодом.
Вместо этого проверка получает `ParamProxy`, который записывает каждое
обращение. Один прогон по принятым значениям — и мы знаем, что каждая проверка
прочитала на самом деле.

Дальше по каждому угаданному параметру гоняются только те проверки, которые его
читали, и только по краям его диапазона. Проверка, сменившая вердикт, —
чувствительна к этому неизвестному; остальные к нему безразличны.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace

from datum.checks.result import CheckResult, CheckRun
from datum.doc.params import Param, ParamProxy, ParamRegistry
from datum.doc.units import from_canonical

__all__ = ["CheckSpec", "Runner", "Sensitivity", "SensitivityRow", "JointRow",
           "run_checks", "sweep_assumed", "MAX_JOINT_PAIRS"]

#: Проверка — функция от параметров, возвращающая один результат или несколько.
CheckFn = Callable[[ParamProxy], "CheckResult | Sequence[CheckResult]"]


@dataclass(frozen=True, slots=True)
class CheckSpec:
    id: str
    fn: CheckFn
    title: str = ""
    group: str = ""


class _NoResult(Exception):
    """Проверка отработала, но не вернула ни одного результата."""


class CheckRefused(Exception):
    """Проверку нельзя считать с такими входами (сказано, почему). В отчёте —
    ошибка без «проверка упала»: проверка цела, неверен вход."""


def _nan_is_not_a_pass(r: CheckResult) -> CheckResult:
    """«Прошло» с nan в значении или пороге — неправда: любое сравнение с nan
    ложно, и проверка, написанная как «упало, если меньше порога», проходила
    сама собой. Такой ответ становится ошибкой. Бесконечность — законна
    («предела нет», «запас бесконечный»), её не трогаем."""
    from datum.finite import non_finite

    if r.status != "pass":
        return r
    bad = (non_finite(r.value, "значение", allow_inf=True)
           or non_finite(r.threshold, "порог", allow_inf=True))
    if bad is None:
        return r
    where, number = bad
    return replace(r, status="error", severity="blocking",
                   message=f"{r.message} — НЕ ПРОВЕРЕНО: {where} {number} — не число, "
                           f"сравнение с ним ничего не значит")


def _soft_fail(r: CheckResult) -> CheckResult:
    """Провал НЕблокирующей проверки — предупреждение, а не отказ.

    `severity: warn` в checks.yaml прежде ни на что не влияло: проверка
    оставалась FAIL, и проект «не готов к печати» из-за того, что автор
    объявил предупреждением (найдено на Gear Cube: устойчивость и опоры скалы).
    Так уже вела себя `overhang` каталога — сама отвечала «warn»; теперь это
    правило одно на всех, и на проектные проверки тоже. Ошибка (`error`) —
    не провал: сломанная проверка не прячется ни за какой важностью."""
    if r.status == "fail" and r.severity in ("warn", "info"):
        return replace(r, status="warn")
    return r


def _as_list(x) -> list[CheckResult]:
    """Привести ответ проверки к списку результатов — или отказать внятно.

    Ответ `None` — почти всегда забытый `return` в одной из ветвей проектной
    проверки. Раньше он превращался в пустой список, проверка исчезала из
    отчёта, и прогон печатал «ИТОГ: OK». Теперь это ошибка, видимая в отчёте.

    Любой итерируемый (в том числе генератор) принимается: раньше генератор
    доезжал до `replace()` и ронял весь прогон, а не одну проверку.
    """
    if x is None:
        raise _NoResult("проверка ничего не вернула — вероятно, забыт return "
                        "в одной из ветвей")
    if isinstance(x, CheckResult):
        return [x]
    if isinstance(x, (str, bytes, dict)):
        raise TypeError(f"проверка вернула {type(x).__name__}, а ждали CheckResult "
                        f"или список их")
    try:
        items = list(x)
    except TypeError:
        raise TypeError(f"проверка вернула {type(x).__name__}, а ждали CheckResult "
                        f"или список их") from None
    bad = sorted({type(i).__name__ for i in items if not isinstance(i, CheckResult)})
    if bad:
        raise TypeError(f"в ответе проверки не только CheckResult: {', '.join(bad)}")
    return items


def _run_one(spec: CheckSpec, registry: ParamRegistry,
             overrides: dict[str, float] | None = None) -> tuple[list[CheckResult], frozenset[str]]:
    """Выполнить одну проверку, вернув результаты и фактически прочитанные параметры."""
    proxy = registry.proxy(overrides)
    t0 = time.perf_counter()

    def failed(message: str) -> list[CheckResult]:
        return [CheckResult(
            id=spec.id, title=spec.title or spec.id, group=spec.group or "",
            status="error", message=f"{spec.id}: {message}", severity="blocking",
            duration_ms=(time.perf_counter() - t0) * 1000)]

    from datum.build.worker import stage

    try:
        with stage(f"проверка {spec.id}"):
            results = _as_list(spec.fn(proxy))
    except (_NoResult, CheckRefused) as e:
        results = failed(str(e))
    except Exception as e:                       # проверка не должна ронять прогон
        from datum.build.hints import with_hint

        results = failed(with_hint(f"проверка упала — {type(e).__name__}: {e}", e))
    results = [_soft_fail(_nan_is_not_a_pass(r)) for r in results]
    if not results:
        # Пустой список — законно (скажем, проверять по перечню нечего), но
        # молча исчезать проверке нельзя: так она выглядит пропущенной.
        results = [CheckResult(
            id=spec.id, title=spec.title or spec.id, group=spec.group or "",
            status="skip", message=f"{spec.id}: проверка не вернула ни одного результата",
            severity="info", duration_ms=(time.perf_counter() - t0) * 1000)]
    reads = proxy.reads

    # Транзитивно доводим зависимости: прочитан производный параметр — значит
    # проверка зависит и от всего, из чего он считается.
    deps: set[str] = set(reads)
    for name in reads:
        deps |= registry.depends_on(name)

    out = []
    for r in results:
        merged = tuple(sorted(set(r.depends_on) | deps))
        out.append(replace(r, depends_on=merged,
                           group=r.group or spec.group or "",
                           title=r.title or spec.title or spec.id))
    return out, frozenset(deps)


def run_checks(specs: Iterable[CheckSpec], registry: ParamRegistry,
               overrides: dict[str, float] | None = None) -> CheckRun:
    """Прогнать набор проверок по принятым (или подставленным) значениям."""
    results: list[CheckResult] = []
    for spec in specs:
        res, _ = _run_one(spec, registry, overrides)
        results.extend(res)
    return CheckRun(tuple(results))


# --------------------------------------------------------------- чувствительность
@dataclass(frozen=True, slots=True)
class SensitivityRow:
    param: str
    check_id: str
    baseline_status: str
    samples: tuple[tuple[float, str, float | None], ...]   # (значение, вердикт, величина)

    @property
    def flips(self) -> bool:
        """Вердикт меняется в пределах диапазона неизвестного."""
        return len({s[1] for s in self.samples} | {self.baseline_status}) > 1

    @property
    def spread(self) -> float | None:
        vals = [s[2] for s in self.samples if s[2] is not None]
        return None if len(vals) < 2 else max(vals) - min(vals)


@dataclass(frozen=True, slots=True)
class JointRow:
    """Пара неизвестных, решающая дело ТОЛЬКО ВМЕСТЕ.

    Каждое по отдельности вердикт не меняет, а угол диапазонов — меняет:
    зацеп = губка − зазор проходит и при тонкой губке, и при широком зазоре,
    но не при обоих сразу.
    """

    params: tuple[str, str]
    check_id: str
    baseline_status: str
    samples: tuple[tuple[tuple[float, float], str, float | None], ...]

    @property
    def flips(self) -> bool:
        return len({s[1] for s in self.samples} | {self.baseline_status}) > 1


#: Сколько пар неизвестных перебирать углами. Каждая пара — четыре прогона
#: каждой проверки, читающей оба; сверх предела отчёт говорит, что перебор
#: неполон, а не молчит.
MAX_JOINT_PAIRS = 45


@dataclass(frozen=True, slots=True)
class Sensitivity:
    rows: tuple[SensitivityRow, ...] = ()
    baseline: CheckRun = field(default_factory=CheckRun)
    checked_params: tuple[str, ...] = ()
    registry: ParamRegistry | None = None
    joint: tuple[JointRow, ...] = ()
    #: Пары, которые НЕ перебирались из-за предела MAX_JOINT_PAIRS.
    joint_skipped: int = 0

    def for_param(self, name: str) -> tuple[SensitivityRow, ...]:
        return tuple(r for r in self.rows if r.param == name)

    def sensitive_checks(self, name: str) -> tuple[str, ...]:
        return tuple(sorted({r.check_id for r in self.for_param(name) if r.flips}))

    @property
    def critical_params(self) -> tuple[str, ...]:
        """Неизвестные, от которых вердикт действительно зависит — по одному
        или вместе с другим."""
        single = {r.param for r in self.rows if r.flips}
        joint = {name for j in self.joint if j.flips for name in j.params}
        return tuple(sorted(single | joint))

    @property
    def harmless_params(self) -> tuple[str, ...]:
        """Неизвестные, менять которые в пределах диапазона ни на что не влияет."""
        return tuple(p for p in self.checked_params if p not in self.critical_params)

    @property
    def blocking_harmless(self) -> tuple[str, ...]:
        """Блокируют печать, хотя развёртка их не чувствует.

        Противоречие, а не ошибка: пометку ставит человек, и у неё могут быть
        причины вне проверок. Но молча держать проект «не готовым» из-за числа,
        не меняющего ни одного вердикта, значит приучать не смотреть на пометку.
        """
        if self.registry is None:
            return ()
        return tuple(n for n in self.harmless_params
                     if n in self.registry and self.registry[n].blocking)

    def report(self) -> str:
        lines: list[str] = []
        if not self.checked_params:
            return "Угаданных параметров нет — мерить нечего."
        crit, harmless = self.critical_params, self.harmless_params
        if crit:
            lines.append("ИЗМЕРИТЬ В ПЕРВУЮ ОЧЕРЕДЬ:")
            for name in crit:
                p = self.registry[name] if self.registry else None
                rng = ""
                if p is not None and p.range:
                    lo, hi = (from_canonical(v, p.unit) for v in p.range)
                    now = f"{p.display:g} {p.unit}".rstrip()
                    rng = f" (сейчас {now}, диапазон {lo:g}…{hi:g})"
                lines.append(f"  {name}{rng}")
                if p is not None and p.measure_hint:
                    lines.append(f"      как мерить: {p.measure_hint}")
                for check_id in self.sensitive_checks(name):
                    row = next(r for r in self.for_param(name) if r.check_id == check_id)
                    seq = ", ".join(
                        f"{from_canonical(v, p.unit) if p else v:g}→{st}" for v, st, _ in row.samples)
                    lines.append(f"      {check_id}: {seq}")
        joint = [j for j in self.joint if j.flips]
        if joint:
            lines.append("СОВМЕСТНО (по отдельности безразличны, вместе решают):")
            for j in joint:
                a, b = j.params
                bad = [f"{a}={va:g}, {b}={vb:g}→{st}" for (va, vb), st, _ in j.samples
                       if st != j.baseline_status]
                lines.append(f"  {a} + {b} — {j.check_id}: " + "; ".join(bad))
        if harmless:
            how = "" if self.joint_skipped else ", по одному и попарно"
            lines.append(f"Нечувствительно (в пределах заявленного диапазона{how}):")
            lines.append("  " + ", ".join(harmless))
        if self.blocking_harmless:
            lines.append("Помечены blocking, но ни на что не влияют: "
                         + ", ".join(self.blocking_harmless))
            lines.append("  пока пометка стоит, проект «не готов к печати» из-за числа, "
                         "которое вердикта не меняет; снять ли её — решает человек")
        if self.joint_skipped:
            lines.append(f"Попарно проверено не всё: {self.joint_skipped} пар сверх предела "
                         f"{MAX_JOINT_PAIRS} не перебирались")
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {
            "critical": list(self.critical_params),
            "harmless": list(self.harmless_params),
            "blocking_harmless": list(self.blocking_harmless),
            "rows": [{"param": r.param, "check": r.check_id, "flips": r.flips,
                      "baseline": r.baseline_status,
                      "samples": [{"value": v, "status": s, "measured": m}
                                  for v, s, m in r.samples]}
                     for r in self.rows],
            "joint": [{"params": list(j.params), "check": j.check_id, "flips": j.flips,
                       "baseline": j.baseline_status,
                       "samples": [{"values": list(v), "status": st, "measured": m}
                                   for v, st, m in j.samples]}
                      for j in self.joint],
            "joint_skipped": self.joint_skipped,
        }


def sweep_assumed(specs: Iterable[CheckSpec], registry: ParamRegistry, *,
                  params: Sequence[str] | None = None) -> Sensitivity:
    """Прогнать проверки по краям диапазонов угаданных параметров.

    Возвращает, какая проверка от какого неизвестного зависит — и, что важнее,
    какие неизвестные ни на что не влияют и мерить их не нужно.
    """
    specs = list(specs)

    # 1. Опорный прогон: заодно узнаём, что каждая проверка реально прочитала.
    baseline_results: list[CheckResult] = []
    spec_deps: dict[str, frozenset[str]] = {}
    per_spec_baseline: dict[str, list[CheckResult]] = {}
    for spec in specs:
        res, deps = _run_one(spec, registry)
        baseline_results.extend(res)
        spec_deps[spec.id] = deps
        per_spec_baseline[spec.id] = res
    baseline = CheckRun(tuple(baseline_results))

    targets = [p for p in registry.unknowns
               if params is None or p.name in set(params)]

    rows: list[SensitivityRow] = []
    for p in targets:
        # Принятое значение из развёртки исключается — его уже дал опорный
        # прогон. Сравнивать с ним напрямую: прежнее `p.value or v` при
        # значении 0 подставляло саму точку, выкидывало ВСЕ точки, и параметр,
        # решающий вердикт, попадал в «нечувствительно».
        centre = p.value
        points = [v for v in p.range_points()
                  if centre is None or abs(v - centre) > 1e-12]
        if not points:
            continue
        for spec in specs:
            if p.name not in spec_deps[spec.id]:
                continue                       # проверка этого параметра не читала
            base_by_id = {r.id: r for r in per_spec_baseline[spec.id]}
            samples_by_id: dict[str, list[tuple[float, str, float | None]]] = {
                rid: [] for rid in base_by_id}
            for value in points:
                res, _ = _run_one(spec, registry, {p.name: value})
                for r in res:
                    samples_by_id.setdefault(r.id, []).append((value, r.status, r.value))
            for rid, samples in samples_by_id.items():
                base = base_by_id.get(rid)
                rows.append(SensitivityRow(
                    param=p.name, check_id=rid,
                    baseline_status=base.status if base else "skip",
                    samples=tuple(samples)))

    # 3. Попарно — углы диапазонов для пар, которые читает одна проверка и
    # каждая из которых по отдельности её вердикт не меняет. Без этого
    # «зацеп = губка − зазор» объявлял оба неизвестных безразличными, хотя
    # тонкая губка при широком зазоре — провал.
    flipped = {(r.param, r.check_id) for r in rows if r.flips}
    by_name = {p.name: p for p in targets}
    names = [p.name for p in targets]
    pairs = [(a, b) for i, a in enumerate(names) for b in names[i + 1:]
             if any(a in spec_deps[sp.id] and b in spec_deps[sp.id] for sp in specs)]
    skipped = max(0, len(pairs) - MAX_JOINT_PAIRS)
    joint: list[JointRow] = []
    for a, b in pairs[:MAX_JOINT_PAIRS]:
        ra, rb = by_name[a].range, by_name[b].range
        if not ra or not rb:
            continue
        corners = [(va, vb) for va in ra for vb in rb]
        for spec in specs:
            if a not in spec_deps[spec.id] or b not in spec_deps[spec.id]:
                continue
            base_by_id = {r.id: r for r in per_spec_baseline[spec.id]}
            samples_by_id: dict[str, list] = {rid: [] for rid in base_by_id}
            for va, vb in corners:
                res, _ = _run_one(spec, registry, {a: va, b: vb})
                for r in res:
                    samples_by_id.setdefault(r.id, []).append(((va, vb), r.status, r.value))
            for rid, samples in samples_by_id.items():
                if (a, rid) in flipped or (b, rid) in flipped:
                    continue                 # уже найдено по одному
                base = base_by_id.get(rid)
                joint.append(JointRow(params=(a, b), check_id=rid,
                                      baseline_status=base.status if base else "skip",
                                      samples=tuple(samples)))

    return Sensitivity(rows=tuple(rows), baseline=baseline,
                       checked_params=tuple(p.name for p in targets),
                       registry=registry, joint=tuple(joint), joint_skipped=skipped)


@dataclass
class Runner:
    """Удобная обёртка: набор проверок плюс реестр."""

    registry: ParamRegistry
    specs: list[CheckSpec] = field(default_factory=list)

    def add(self, id: str, fn: CheckFn, *, title: str = "", group: str = "") -> "Runner":
        self.specs.append(CheckSpec(id=id, fn=fn, title=title, group=group))
        return self

    def run(self, overrides: dict[str, float] | None = None) -> CheckRun:
        return run_checks(self.specs, self.registry, overrides)

    def sweep(self, params: Sequence[str] | None = None) -> Sensitivity:
        return sweep_assumed(self.specs, self.registry, params=params)
