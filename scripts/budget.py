#!/usr/bin/env python3
"""Бюджеты времени — пункт I приёмочного плана.

Запуск: `.venv/bin/python scripts/budget.py`

План обещал сроки: холодная пересборка проекта, горячая пересборка одной
детали, полный прогон проверок. Пока их не меряют, это не обещание, а надежда —
и особенно здесь, где половина проверок считает булевы операции пачками, а
теперь ещё и трёхмерные задачи.

ЧИСЛА БЕРУТСЯ С ПОРТИРОВАННЫХ ПРОЕКТОВ, а не с синтетики: `latch` — защёлка с
геометрией и контактом, `dispenser` — аналитика и МКЭ. Разные нагрузки, и
узкое место у них разное.

ОГОВОРКА, БЕЗ КОТОРОЙ ОТЧЁТ ЛЬСТИЛ БЫ. Портированные детали просты — коробки
и листы, — и собираются за миллисекунды: бюджет в 20 секунд на них показывал
«0.00 с» и не стерёг ничего. Он писался под `phone-case-gears` с шестернями,
где зубья строились булевыми в цикле. Такого проекта в примерах нет, поэтому
он СОЗДАЁТСЯ здесь же во временном каталоге: четыре шестерни по 48…120 зубьев,
каждый зуб — отдельное тело, как в корпусе, плюс ступицы, облегчения и
проверки на них. Отдельно меряется катушка со 168 канавками — там же видна
цена стража.

Код возврата: 0 — все бюджеты выдержаны, 1 — какой-то превышен.
"""

from __future__ import annotations

import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "py"))

#: Бюджеты из плана, секунды. Первые два — на проект целиком.
BUDGETS = {
    "холодная сборка": 20.0,
    "горячая сборка одной детали": 2.0,
    "полный прогон проверок": 60.0,
}


def _time(seconds: float) -> str:
    """Короткое — в мс: «0.00 с» не отличало 1 мс от 4 мс."""
    return f"{seconds * 1000:7.1f} мс" if seconds < 1.0 else f"{seconds:7.2f} с "


def timed(fn):
    start = time.perf_counter()
    value = fn()
    return value, time.perf_counter() - start


def measure(project: pathlib.Path) -> list[tuple[str, float, float, str]]:
    from datum.doc.project import Project

    out: list[tuple[str, float, float, str]] = []
    pr = Project.open(project)

    # Холодная: кеш не используется вовсе, всё считается заново.
    built, cold = timed(lambda: pr.build(use_cache=False))
    bad = [n for n, r in built.items() if not r.ok]
    if bad:
        raise SystemExit(f"{project.name}: не собрались детали {bad}")
    out.append(("холодная сборка", cold, BUDGETS["холодная сборка"],
                f"{len(built)} деталей"))

    # Горячая: одна деталь, кеш включён. Меряем ПОВТОРНУЮ сборку — именно так
    # выглядит правка параметра, не затронувшая эту деталь.
    one = sorted(built)[0]
    pr.build([one])                                   # прогреть кеш
    _, hot = timed(lambda: pr.build([one]))
    out.append(("горячая сборка одной детали", hot,
                BUDGETS["горячая сборка одной детали"], one))

    run, checks = timed(pr.run_checks)
    out.append(("полный прогон проверок", checks, BUDGETS["полный прогон проверок"],
                f"{len(run.results)} проверок, {'ОК' if run.ok else 'есть провалы'}"))
    return out


def heavy() -> list[tuple[str, float, float, str]]:
    """Самый тяжёлый случай плана: 168 булевых на одной детали."""
    from datum.geom.booleans import diff
    from datum.geom.guard import checkpoint
    from datum.geom.measure import vol
    from datum.geom.prims import box, cyl_z
    from datum.geom.xform import array_polar

    n = 168
    solid, expression = timed(
        lambda: diff(cyl_z(80.0, 0.0, 20.0),
                     [array_polar(box(38, 42, -1.2, 1.2, 2, 18), n)]))
    _, forced = timed(lambda: vol(solid))
    _, guarded = timed(lambda: checkpoint(solid))
    work = expression + forced
    # Порог стража — доля сборки, но не меньше 5 мс: при сборке в 28 мс пять
    # процентов — это 1.4 мс, и печаталось «≤ 0.00», а исход решал шум таймера.
    return [
        ("деталь со 168 канавками", work, 2.0, "выражение плюс расчёт"),
        ("страж на ней же", guarded, max(0.05 * work, 0.005),
         f"{100 * guarded / max(work, 1e-9):.1f} % от сборки — план обещал доли процента"),
    ]


def field() -> list[tuple[str, float, float, str]]:
    """Скульптура полем на сетке размером с подставку под мышь (0.4 мм).

    Там поверхность через `Manifold.level_set` с выборкой на Python стоила
    ~45 с на каждую сборку, и агент упирался в предел времени. Здесь весь
    конвейер — предел уклона, переизмерение, узор, поверхность.
    """
    import numpy as np

    from datum.geom import field as F

    g = F.Grid.around((0, 0, 0), (108, 90, 56), 0.4)
    X, Y, Z = g.mesh()
    shape = F.Field(g, ((25 - np.sqrt((X - 54) ** 2 + (Y - 45) ** 2 + (Z - 30) ** 2))
                        + 2 * np.sin(X / 3) * np.sin(Y / 4)).astype(np.float32))
    limited, t_limit = timed(lambda: F.limit_overhang(shape, 45))
    exact, t_dist = timed(lambda: F.redistance(limited))
    relief, t_pat = timed(lambda: F.surface_pattern(exact, F.diagrid(8.0, axis_xy=(54, 45, 25)), 1.0))
    solid, t_mesh = timed(lambda: F.mesh(relief, simplify=0.02))
    total = t_limit + t_dist + t_pat + t_mesh
    nodes = g.shape[0] * g.shape[1] * g.shape[2]
    return [
        ("поверхность поля", t_mesh, 3.0, f"{nodes / 1e6:.1f} млн узлов, было ~45 с через level_set"),
        ("конвейер скульптуры целиком", total, 30.0,
         f"предел {t_limit:.1f} + переизмерение {t_dist:.1f} + узор {t_pat:.1f} с"),
    ]


GEARS_PARAMS = """\
MODULE: {value: 1.0, unit: mm, provenance: chosen, reason: "модуль зацепления"}
FACE:   {value: 8.0, unit: mm, provenance: chosen, reason: "ширина венца"}
BORE:   {value: 8.0, unit: mm, provenance: chosen, reason: "под вал"}
BACKLASH: {value: 0.15, unit: mm, provenance: chosen, reason: "боковой зазор"}
"""

GEARS_PART = '''\
"""Четыре шестерни в зацеплении. Зуб — отдельное тело, как в корпусе."""

import math

from datum.build import P, part
from datum.geom import array_polar, cyl_z, diff, move, prism_xy, rotate, union

TEETH = (48, 72, 96, 120)


def _tooth(z):
    m = P.MODULE
    r = m * z / 2
    root, tip = r - 1.25 * m, r + m
    half_pitch = math.pi * m / 4 - P.BACKLASH / 2
    flank = []
    for i in range(7):
        rho = root + (tip - root) * i / 6
        # Эвольвента, упрощённая до сужения к вершине: ширина у ножки больше,
        # у вершины меньше. Число точек — как у настоящего профиля.
        half = half_pitch * (1.35 - 0.7 * i / 6)
        flank.append((rho, half))
    pts = [(x, -y) for x, y in flank] + [(x, y) for x, y in reversed(flank)]
    return prism_xy([(x - 0.3, y) if k in (0, len(pts) - 1) else (x, y)
                     for k, (x, y) in enumerate(pts)], 0, P.FACE)


def _gear(z, x, turn):
    m = P.MODULE
    body = cyl_z(m * z - 2.5 * m + 0.6, 0, P.FACE, n=256)
    teeth = array_polar(_tooth(z), z)
    lighten = array_polar(cyl_z(m * z * 0.18, -1, P.FACE + 1,
                                x=m * z * 0.28, y=0), 6)
    gear = diff(union([body, teeth]), [cyl_z(P.BORE, -1, P.FACE + 1), lighten])
    return move(rotate(gear, rz=turn), dx=x)


def _centres():
    xs, x = [], 0.0
    for a, b in zip(TEETH, TEETH[1:]):
        xs.append(x)
        x += P.MODULE * (a + b) / 2 + P.BACKLASH
    xs.append(x)
    return xs


for _i, _z in enumerate(TEETH):
    def _make(z=_z, i=_i):
        # Параметры есть только при сборке. Каждая вторая повёрнута на
        # полшага: иначе зубья соседей стояли бы вершина к вершине.
        return _gear(z, _centres()[i], 180.0 / z if i % 2 else 0.0)
    part(f"g{_z}", print_up="+Z")(_make)
'''

GEARS_CHECKS = "".join(
    f"""- id: g{z}.целостность
  use: single_body
  args: {{part: "@g{z}"}}
- id: g{z}.стенка
  use: min_wall
  args: {{part: "@g{z}", wall: 0.8, samples: 12}}
- id: g{z}.нависания
  use: overhang
  args: {{part: "@g{z}"}}
""" for z in (48, 72, 96, 120)) + "".join(
    f"""- id: g{a}-g{b}.зазор
  use: clearance
  args: {{a: "@g{a}", b: "@g{b}", min_mm: 0.05}}
""" for a, b in ((48, 72), (72, 96), (96, 120)))


def gears_project(root: pathlib.Path) -> pathlib.Path:
    """Тяжёлый синтетический проект — такой, под который писался бюджет."""
    for sub in ("params", "parts", "checks"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "datum.toml").write_text('schema = 1\nname = "gears"\n', encoding="utf-8")
    (root / "params" / "base.yaml").write_text(GEARS_PARAMS, encoding="utf-8")
    (root / "parts" / "gears.py").write_text(GEARS_PART, encoding="utf-8")
    (root / "checks" / "checks.yaml").write_text(GEARS_CHECKS, encoding="utf-8")
    return root


def main() -> int:
    import tempfile

    tmp = tempfile.TemporaryDirectory(prefix="datum-budget-")
    projects = [ROOT / "examples" / name for name in ("latch", "dispenser", "spool", "sculpt")]
    projects.append(gears_project(pathlib.Path(tmp.name) / "gears"))
    exceeded = 0
    for project in projects:
        if not project.is_dir():
            # Нет примера — это провал замера, а не повод его пропустить:
            # иначе переименование examples/ делало бюджет зелёным.
            print(f"ПЛОХО нет проекта {project} — мерить нечего")
            exceeded += 1
            continue
        print(f"== {project.name} ==")
        for name, taken, budget, note in measure(project):
            ok = taken <= budget
            exceeded += 0 if ok else 1
            mark = "OK  " if ok else "ПЛОХО"
            print(f"  {mark} {name:30} {_time(taken)} "
                  f"{'≤' if ok else '>'} {budget:5.0f} с   ({note})")
        print()

    print("== тяжёлый случай ==")
    for name, taken, budget, note in heavy() + field():
        ok = taken <= budget
        exceeded += 0 if ok else 1
        print(f"  {'OK  ' if ok else 'ПЛОХО'} {name:30} {_time(taken)} "
              f"{'≤' if ok else '>'} {_time(budget).strip()}   ({note})")
    print()
    print(f"Превышено бюджетов: {exceeded}")
    return 1 if exceeded else 0


if __name__ == "__main__":
    raise SystemExit(main())
