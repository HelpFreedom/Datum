"""«Почему красное» и подбор параметра.

Вопросы владельца в сессии руки: «Почему проверки красные?», «Почему просто
не усилить слабые части?». Проверка говорит, ЧТО не так и какие параметры
читала, но не говорит, какой из них и на сколько двигать: агент подбирал это
перебором — правка, полный прогон, ещё правка.

  * `check_explain` — у каждого параметра, который проверка читает, сдвиг
    вверх и вниз и то, как от этого меняется запас: что решает дело, а что
    двигать бесполезно;
  * `param_solve` — значение ОДНОГО параметра, при котором проверка проходит,
    ближайшее к нынешнему (деление пополам в заданных пределах).

Ни та, ни другая ничего не записывают: решение — за человеком и агентом
(`param_set`). Прогон идёт подстановкой значений, как у `sweep_assumed`, и
через кеш проверок: повторные точки не пересчитываются.
"""

from __future__ import annotations

from typing import Annotated, Any

from datum.tools.ops import Project, _err, _guard, open_project
from datum.tools.registry import Arg, op

__all__ = ["check_explain", "param_solve"]

#: Сколько параметров разбирает `check_explain` (каждый — два прогона проверки).
MAX_EXPLAINED = 24
#: Сколько шагов деления пополам делает `param_solve`.
SOLVE_STEPS = 14


def _result(pr, check_id: str, overrides: dict[str, float] | None = None):
    """Результат проверки `check_id` при подстановке — или None, если его нет."""
    run = pr.run_checks(overrides, only=[check_id])
    exact = [r for r in run.results if r.id == check_id]
    if exact:
        return exact[0]
    return run.worst


def _adjustable(pr, names) -> tuple[list, list[str]]:
    """Параметры, которые можно двигать (не производные, с числом), и остальные."""
    movable, fixed = [], []
    for name in names:
        if name not in pr.params:
            continue
        p = pr.params[name]
        if p.provenance == "derived" or p.value is None:
            fixed.append(name)
        else:
            movable.append(p)
    return movable, fixed


def _state(r) -> str:
    return {"pass": "проходит", "fail": "НЕ проходит", "warn": "предупреждение",
            "error": "ошибка", "skip": "пропуск"}.get(r.status, r.status)


@op("check_explain", cli_name="explain", group="проверки", isolate=True,
    title="Почему проверка красная",
    summary=("Какой параметр решает исход проверки: каждый прочитанный ею параметр "
             "сдвигается вверх и вниз (на долю step или до края своего диапазона), и "
             "показывается, как меняется запас и вердикт. Отвечает на «что подвинуть, "
             "чтобы прошло» и «что двигать бесполезно». Ничего не записывает."))
async def check_explain(
    project: Project,
    check: Annotated[str, Arg(help="id проверки (как в отчёте check_run)")],
    step: Annotated[float, Arg(help="на какую долю значения сдвигать параметр")] = 0.1,
) -> dict[str, Any]:
    def run():
        if not 0.001 <= step <= 1.0:
            return _err(f"step {step:g}: доля сдвига от 0.001 до 1")
        from datum.doc.units import from_canonical, to_canonical

        pr = open_project(project)
        base = _result(pr, check)
        if base is None:
            return _err(f"проверка {check!r} не дала результата")
        movable, fixed = _adjustable(pr, base.depends_on)
        if not movable:
            return {"check": base.id, "status": base.status, "rows": [],
                    "report": (f"{base.line().strip()}\n{base.id} не читает ни одного "
                               f"параметра, который можно подвинуть"
                               + (f" (производные: {', '.join(fixed)})" if fixed else ""))}
        skipped = [p.name for p in movable[MAX_EXPLAINED:]]
        rows = []
        for p in movable[:MAX_EXPLAINED]:
            now = p.display
            delta = abs(now) * step if abs(now) > 1e-12 else step
            points = []
            for sign in (-1.0, 1.0):
                value = now + sign * delta
                if p.range:
                    lo, hi = (from_canonical(v, p.unit) for v in p.range)
                    value = min(max(value, lo), hi)
                if abs(value - now) < 1e-12:
                    points.append(None)
                    continue
                r = _result(pr, check, {p.name: to_canonical(value, p.unit)})
                points.append({"value": round(value, 6), "status": r.status,
                               "margin": r.margin, "measured": r.value})
            margins = [pt["margin"] for pt in points if pt and pt["margin"] is not None]
            effect = (max(margins) - min(margins)) if len(margins) == 2 else (
                abs(margins[0] - base.margin) if margins and base.margin is not None else None)
            flips = any(pt and pt["status"] != base.status for pt in points)
            rows.append({"param": p.name, "unit": p.unit, "now": round(now, 6),
                         "provenance": p.provenance, "down": points[0], "up": points[1],
                         "effect": effect, "flips": flips})
        rows.sort(key=lambda r: (not r["flips"], -(r["effect"] or 0.0)))

        def cell(pt) -> str:
            if pt is None:
                return "— (край диапазона)"
            m = "" if pt["margin"] is None else f", запас {pt['margin'] * 100:+.1f} %"
            return f"{pt['value']:g}: {_state_word(pt['status'])}{m}"

        lines = [base.line().strip(),
                 f"{base.id}: {_state(base)}"
                 + (f", запас {base.margin * 100:+.1f} % от порога" if base.margin is not None
                    else ""),
                 "", "параметр — меньше / больше (вердикт и запас):"]
        for r in rows:
            mark = "  ! МЕНЯЕТ ВЕРДИКТ" if r["flips"] else ""
            weight = ("не влияет" if not r["effect"] or r["effect"] < 1e-9 else
                      f"влияние {r['effect'] * 100:.1f} % запаса")
            lines.append(f"  {r['param']} = {r['now']:g} {r['unit']} ({r['provenance']}): "
                         f"{cell(r['down'])} / {cell(r['up'])} — {weight}{mark}")
        if fixed:
            lines.append(f"производные (двигаются через свои формулы): {', '.join(fixed)}")
        if skipped:
            lines.append(f"не разобраны (параметров больше {MAX_EXPLAINED}): "
                         f"{', '.join(skipped)}")
        deciding = [r["param"] for r in rows if r["flips"]]
        if deciding:
            lines.append(f"\nвердикт меняют: {', '.join(deciding)} — точное значение подберёт "
                         f"param_solve(check={base.id!r}, param=…)")
        elif not base.ok:
            lines.append("\nни один сдвиг на этом шаге вердикта не меняет: возьмите step "
                         "больше или меняйте конструкцию, а не число")
        return {"check": base.id, "status": base.status, "margin": base.margin,
                "rows": rows, "report": "\n".join(lines)}

    return _guard(run)


def _state_word(status: str) -> str:
    return {"pass": "проходит", "fail": "НЕ проходит", "warn": "предупреждение",
            "error": "ошибка", "skip": "пропуск"}.get(status, status)


@op("param_solve", cli_name="solve", group="проверки", isolate=True,
    title="Подобрать параметр под проверку",
    summary=("Найти значение ОДНОГО параметра, при котором проверка проходит, — "
             "ближайшее к нынешнему (делением пополам между lo и hi; не заданы — "
             "диапазон параметра или от половины до двух нынешних значений). Ничего "
             "не записывает: значение примените param_set и прогоните ВСЕ проверки — "
             "подобранное под одну может сломать другую."))
async def param_solve(
    project: Project,
    check: Annotated[str, Arg(help="id проверки")],
    param: Annotated[str, Arg(help="имя параметра, который двигать")],
    lo: Annotated[float | None, Arg(help="нижний предел поиска, в единицах параметра")] = None,
    hi: Annotated[float | None, Arg(help="верхний предел поиска")] = None,
) -> dict[str, Any]:
    def run():
        from datum.doc.units import from_canonical, to_canonical

        pr = open_project(project)
        if param not in pr.params:
            return _err(f"параметра {param!r} нет")
        p = pr.params[param]
        if p.provenance == "derived" or p.value is None:
            return _err(f"{param} — производный ({p.expr}): подбирайте то, из чего он "
                        f"считается")
        now = p.display
        if p.range:
            r_lo, r_hi = (from_canonical(v, p.unit) for v in p.range)
        else:
            r_lo, r_hi = sorted((now * 0.5, now * 2.0)) if abs(now) > 1e-12 else (-1.0, 1.0)
        a = r_lo if lo is None else float(lo)
        b = r_hi if hi is None else float(hi)
        if not a < b:
            return _err(f"пределы поиска {a:g}…{b:g}: нижний обязан быть меньше верхнего")
        if p.range and (a < r_lo - 1e-12 or b > r_hi + 1e-12):
            return _err(f"{param} — догадка с диапазоном [{r_lo:g}, {r_hi:g}] {p.unit}: "
                        f"искать вне его — значит менять саму догадку (param_edit range=…)")

        tried: dict[float, Any] = {}

        def at(value: float):
            key = round(value, 9)
            if key not in tried:
                tried[key] = _result(pr, check, {param: to_canonical(value, p.unit)})
            return tried[key]

        base = _result(pr, check)
        if base is None:
            return _err(f"проверка {check!r} не дала результата")
        if param not in base.depends_on:
            return _err(f"{base.id} не читает {param}: двигать его бесполезно. Читает: "
                        f"{', '.join(base.depends_on) or 'ничего'}")
        head = f"{base.line().strip()}\n{param} сейчас {now:g} {p.unit}: {_state(base)}"
        if base.ok and base.status == "pass":
            return {"check": base.id, "param": param, "now": now, "value": now, "passes": True,
                    "report": head + " — подбирать нечего"}

        # Ищем по обе стороны от нынешнего ближайшее проходящее: сначала край,
        # потом деление пополам между «не проходит» (нынешнее) и «проходит».
        found = []
        for edge in (a, b):
            if abs(edge - now) < 1e-12:
                continue
            if at(edge).status != "pass":
                continue
            bad, good = now, edge
            if not a <= now <= b:
                bad = b if edge == a else a           # нынешнее вне пределов поиска
                if at(bad).status == "pass":
                    found.append((bad, at(bad)))
                    continue
            for _ in range(SOLVE_STEPS):
                mid = (bad + good) / 2
                if at(mid).status == "pass":
                    good = mid
                else:
                    bad = mid
            found.append((good, at(good)))
        if not found:
            ends = ", ".join(f"{e:g}: {_state_word(at(e).status)}" for e in (a, b))
            return {"check": base.id, "param": param, "now": now, "value": None,
                    "passes": False, "tried": len(tried),
                    "report": (head + f"\nв пределах {a:g}…{b:g} {p.unit} проверка не проходит "
                               f"ни на одном краю ({ends}) — этим параметром дело не решить "
                               f"(или вердикт меняется внутри, не на краях: check_explain "
                               f"покажет, что решает)")}
        value, res = min(found, key=lambda f: abs(f[0] - now))
        # Округляем В СТОРОНУ прохождения: значение на самой границе
        # развернётся от шума мешера или округления при записи.
        digits = 3
        rounded = round(value, digits)
        if at(rounded).status != "pass":
            bump = 10 ** -digits * (1 if value > now else -1)
            rounded = round(rounded + bump, digits)
        final = at(rounded)
        if final.status != "pass":
            rounded, final = value, res
        margin = "" if final.margin is None else f", запас {final.margin * 100:+.1f} %"
        return {"check": base.id, "param": param, "now": now, "value": rounded,
                "passes": True, "margin": final.margin, "tried": len(tried),
                "report": (head + f"\nпроходит при {param} = {rounded:g} {p.unit}{margin} "
                           f"(было {now:g}; точек посчитано {len(tried)}). Это граница: "
                           f"возьмите с запасом. Применить — param_set, затем check_run без "
                           f"фильтра: значение, подобранное под одну проверку, может "
                           f"сломать другую")}

    return _guard(run)
