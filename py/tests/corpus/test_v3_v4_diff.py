"""Сравнение версий на настоящих данных: phone-case-gears v3 против v4.

В NOTES.md проекта это велось руками. Здесь тот же вопрос — «что изменилось
между версиями» — задаётся инструменту, и ответ строится из выгруженных сборок:
метрики деталей плюс таблица проверок удержания.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tests.corpus.conftest import load_assembly

DIRS = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]
GAP = {"v3": 0.57, "v4": 0.400}

#: Как детали называются в каждой версии: имена менялись вместе с конструкцией.
NAMES = {
    "v3": {"корпус": "1_shell", "шторка": "2_shutter", "кнопка": "6_button",
           "шестерня1": "3_gear1", "шестерня2": "3_gear2"},
    "v4": {"корпус": "case", "шторка": "shutter", "кнопка": "button",
           "шестерня1": "gear1", "шестерня2": "gear2"},
}


def _snapshot(tag: str, parts: dict, mapping: dict, gap: float):
    """Собрать снимок из выгруженной сборки: метрики деталей и проверки зацепа."""
    from manifold3d import Manifold, OpType

    from datum.checks.motion import escape
    from datum.checks.result import CheckRun
    from datum.doc.snapshot import DENSITY, PartMetrics, Snapshot
    from datum.geom.convert import from_trimesh
    from datum.geom.measure import area, bbox
    from datum.geom.solid import Solid
    from datum.geom.topology import analyze

    metrics: dict[str, PartMetrics] = {}
    solids: dict[str, Solid] = {}
    for human, key in mapping.items():
        if key not in parts:
            continue
        s = Solid.wrap(from_trimesh(parts[key]), name=human, op="import")
        solids[human] = s
        t = analyze(s.manifold)
        lo, hi = bbox(s)
        metrics[human] = PartMetrics(
            name=human, ok=True, result_digest=s.digest(),
            volume_mm3=t.volume, area_mm2=area(s),
            bbox_min=tuple(round(v, 6) for v in lo),
            bbox_max=tuple(round(v, 6) for v in hi),
            bodies=t.bodies, sealed_voids=t.sealed_voids, genus=t.genus,
            triangles=t.triangles,
            mass_g=t.volume / 1000.0 * DENSITY["PLA"])

    rest = Manifold.batch_boolean(
        [from_trimesh(m) for k, m in parts.items()
         if k != mapping["шторка"] and "phone" not in k], OpType.Add)
    holder = Solid.wrap(rest, name="всё остальное", op="union")
    run = CheckRun(tuple(escape(solids["шторка"], holder, DIRS, gap=gap,
                                id_prefix="шторка.зацеп")))

    return Snapshot(
        tag=tag, created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        project="phone-case-gears", parts=metrics, checks=run.as_dict(),
        manifest={"printer": {"material": "PLA"}})


@pytest.fixture(scope="module")
def pair(cad_root):
    base = cad_root / "projects/phone-case-gears/output"
    f3 = base / "v3/phone_case_v3_closed.3mf"
    f4 = base / "phone_case_v4_closed.3mf"
    if not (f3.is_file() and f4.is_file()):
        pytest.skip("нет выгруженных сборок v3/v4")
    return (_snapshot("v3", load_assembly(f3), NAMES["v3"], GAP["v3"]),
            _snapshot("v4", load_assembly(f4), NAMES["v4"], GAP["v4"]))


def test_diff_shows_v4_fixed_what_v3_broke(pair):
    """Главное: переход от v3 к v4 обязан читаться как «починилось», а не наоборот."""
    from datum.doc.diff import diff

    d = diff(*pair)
    assert not d.is_regression, d.report()
    fixed = {c.id for c in d.fixed}
    assert "шторка.зацеп.+Z" in fixed, d.report()
    assert len(fixed) == len(DIRS), "в v4 должны починиться все направления"


def test_reverse_diff_reads_as_regression(pair):
    from datum.doc.diff import diff

    back = diff(pair[1], pair[0])
    assert back.is_regression
    assert len(back.broken) == len(DIRS)


def test_diff_quantifies_the_geometry_change(pair):
    """Корпус v4 заметно массивнее — это видно по метрикам, а не на глаз."""
    from datum.doc.diff import diff

    d = diff(*pair)
    case = next(p for p in d.parts if p.name == "корпус")
    assert case.kind == "changed"
    assert case.dv > 0 and case.dv_frac > 0.5, (
        f"объём корпуса вырос на {case.dv:.0f} мм³ ({100 * case.dv_frac:.0f} %)")


def test_report_is_human_readable(pair):
    from datum.doc.diff import diff

    d = diff(*pair)
    txt = d.report()
    assert "v3 → v4" in txt
    assert "ПОЧИНИЛОСЬ" in txt
    # Когда что-то починилось, итог говорит об этом, а не «регрессий нет»:
    # это взаимоисключающие ветви, и первая содержательнее.
    assert f"Починилось проверок: {len(d.fixed)}" in txt
    assert "Масса:" in txt
