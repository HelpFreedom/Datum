"""Снимки версий и сравнение — претензия №7 («что изменилось между v12 и v13»)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from datum.doc.diff import diff
from datum.doc.project import Project
from datum.doc.snapshot import DENSITY, Snapshot, list_snapshots

ROOT = Path(__file__).resolve().parents[3]
LATCH = ROOT / "examples" / "latch"


@pytest.mark.skipif(not shutil.which("git"), reason="для проверки нужен Git")
def test_snapshot_records_project_head(pr, monkeypatch):
    def git(root, *args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

    from datum.doc.snapshot import _project_git_head

    git(pr.root, "init", "-q")
    assert _project_git_head(pr.root) is None
    git(pr.root, "add", "datum.toml")
    git(pr.root, "-c", "user.name=Datum", "-c", "user.email=test@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "test")
    head = git(pr.root, "rev-parse", "HEAD")
    assert _project_git_head(pr.root / "parts") is None
    monkeypatch.setenv("GIT_DIR", str(pr.root / "absent"))
    snapshot = Snapshot.create(pr, "git")
    assert Snapshot.load(snapshot.save(pr.root)).environment["git_head"] == head
    assert head in snapshot.report()
    monkeypatch.setenv("PATH", "")
    assert _project_git_head(pr.root) is None


@pytest.fixture
def workdir(latch):
    """Копия примера, чтобы снимки не сорили в репозитории."""
    return latch


@pytest.fixture
def pr(workdir):
    return Project.open(workdir)


# ------------------------------------------------------------------ снимок
def test_snapshot_captures_metrics(pr):
    s = Snapshot.create(pr, "проба")
    assert set(s.parts) == {"корпус", "крышка"}
    lid = s.parts["крышка"]
    assert lid.ok and lid.bodies == 1 and lid.sealed_voids == 0
    assert lid.volume_mm3 == pytest.approx(55.4 * 35.4 * 3.0, rel=1e-6)
    assert lid.result_digest and len(lid.result_digest) == 64
    assert lid.source_digest, "отпечаток исходника нужен, чтобы отличать правку кода"


def test_mass_is_the_printed_mass_the_same_as_part_mass(pr):
    """Масса в снимке — по настройкам печати, той же функцией, что `part_mass`,
    симуляция и ведомость. Прежде — объём × плотность: «масса» в версиях была в
    полтора-два раза больше «массы» в `part_mass` (аудит 2026-11-21, §6.2.7)."""
    from datum.doc.materials import CATALOG
    from datum.fab.mass import cached_print_mass

    s = Snapshot.create(pr, "масса")
    lid = s.parts["крышка"]
    solid = pr.build(["крышка"])["крышка"].solid
    want = cached_print_mass(solid, pr.print_settings("крышка"),
                             up=pr.parts["крышка"].print_up,
                             cache_dir=pr.root / ".datum" / "mass").mass_g
    assert lid.mass_by == "печать" and lid.mass_g == pytest.approx(want, rel=1e-9)
    assert lid.mass_g < lid.volume_mm3 / 1000.0 * CATALOG["PLA"].density, "не сплошное тело"
    assert s.mass_by_print and "Итого пластика" in s.report()
    assert "сплошным телом" not in s.report()
    again = Snapshot.load(s.save(pr.root))
    assert again.parts["крышка"].mass_by == "печать"


def test_an_old_snapshot_without_the_mass_kind_is_not_compared_by_mass(pr):
    """Снимок старше поля `mass_by` считал объём × плотность: разница масс с
    новым была бы про способ счёта, а не про деталь."""
    from datum.doc.diff import diff

    new = Snapshot.create(pr, "новый")
    raw = new.as_dict()
    for part in raw["parts"].values():
        part.pop("mass_by")
        part["mass_g"] = part["volume_mm3"] / 1000.0 * 1.26
    path = pr.root / "старый.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    old = Snapshot.load(path)
    assert not old.mass_by_print and "сплошным телом" in old.report()
    assert "считаны по-разному" in diff(old, new).report()
    assert "считаны по-разному" not in diff(new, new).report()


def test_the_fallback_density_table_is_the_catalog():
    from datum.doc.materials import CATALOG

    # Запасная таблица — из того же каталога: прежняя своя разошлась с ним.
    assert DENSITY["PLA"] == CATALOG["PLA"].density


def test_snapshot_knows_it_is_not_print_ready(pr):
    """Есть незамеренное с пометкой blocking — значит печатать рано."""
    s = Snapshot.create(pr, "готовность")
    assert s.checks["ok"] is True
    assert s.params_meta_blocking() == ["LIP_D"]
    assert s.print_ready is False


def test_snapshot_roundtrip(pr, workdir):
    s = Snapshot.create(pr, "круг")
    path = s.save(workdir)
    assert path.is_file() and path.parent.name == "snapshots"
    back = Snapshot.load(path)
    assert back.tag == s.tag and back.params == s.params
    assert back.parts["корпус"].result_digest == s.parts["корпус"].result_digest
    assert json.loads(path.read_text(encoding="utf-8"))["project"] == "latch"


def test_snapshots_with_the_same_tag_do_not_overwrite(pr, workdir):
    a = Snapshot.create(pr, "одинаковый").save(workdir)
    b = Snapshot.create(pr, "одинаковый").save(workdir)
    assert a != b, "второй снимок не должен затирать первый"
    assert len(list_snapshots(workdir)) == 2


# ------------------------------------------------------------------ сравнение
@pytest.fixture
def pair(pr):
    good = Snapshot.create(pr, "губка 0.8")
    bad = Snapshot.create(pr, "губка 0.3", overrides={"LIP_D": 0.3})
    return good, bad


def test_diff_finds_the_regression(pair):
    d = diff(*pair)
    assert d.is_regression
    broken = {c.id for c in d.broken}
    assert broken == {"крышка.зацеп.+Z", "губка.против_зазора", "крышка.защёлка"}


def test_diff_reports_the_parameter_that_caused_it(pair):
    d = diff(*pair)
    ch = [p for p in d.params if p.name == "LIP_D"]
    assert ch and ch[0].kind == "changed"
    assert ch[0].before == pytest.approx(0.8) and ch[0].after == pytest.approx(0.3)


def test_unchanged_part_is_recognised_by_digest(pair):
    """Крышка не зависит от губки — её отпечаток обязан совпасть."""
    d = diff(*pair)
    lid = next(p for p in d.parts if p.name == "крышка")
    assert lid.kind == "unchanged"
    case = next(p for p in d.parts if p.name == "корпус")
    assert case.kind == "changed" and case.dv < 0


def test_diff_direction_matters(pair):
    good, bad = pair
    back = diff(bad, good)
    assert not back.is_regression
    assert {c.id for c in back.fixed} == {"крышка.зацеп.+Z", "губка.против_зазора", "крышка.защёлка"}


def test_a_module_constant_edit_is_a_code_change(workdir):
    """Тело функции то же, а геометрия другая: правка константы модуля (как и
    помощника в lib/) — тоже «изменён код детали». Прежде снимок сверял только
    тело функции, и правка была видна лишь по объёму."""
    lid = workdir / "parts" / "lid.py"
    before = Snapshot.create(Project.open(workdir), "до")
    text = lid.read_text(encoding="utf-8")
    # Код модуля ВНЕ функции: та же `box`, но крышка на полмиллиметра тоньше.
    thinner = ("from datum.geom import box as _box\n\n\n"
               "def box(x0, x1, y0, y1, z0, z1):\n"
               "    return _box(x0, x1, y0, y1, z0 + 0.5, z1)\n")
    lid.write_text(text.replace("from datum.geom import box\n", thinner), encoding="utf-8")
    after = Snapshot.create(Project.open(workdir), "после")
    a, b = before.parts["крышка"], after.parts["крышка"]
    assert a.source_digest == b.source_digest           # тело функции не тронуто
    assert a.code_digest != b.code_digest and a.volume_mm3 > b.volume_mm3
    change = next(p for p in diff(before, after).parts if p.name == "крышка")
    assert change.kind == "changed" and "изменён код детали" in change.line()


def test_a_snapshot_records_the_installed_datum_version(pr):
    from importlib.metadata import version

    assert Snapshot.create(pr, "версия").environment["datum"] == version("datum")


def test_diff_of_identical_snapshots_is_quiet(pr):
    a, b = Snapshot.create(pr, "a"), Snapshot.create(pr, "b")
    d = diff(a, b)
    assert not d.is_regression and not d.changed_parts
    assert d.params == ()
    assert "Регрессий нет" in d.report()


def test_provenance_change_is_highlighted(pr, workdir):
    """Угаданное стало измеренным — это хорошая новость, её надо видеть."""
    before = Snapshot.create(pr, "до обмера")

    text = (workdir / "params" / "base.yaml").read_text(encoding="utf-8")
    text = text.replace("  provenance: assumed\n", "  provenance: measured\n")
    text = text.replace("  range: [0.3, 1.5]\n", "")
    text = text.replace("  blocking: true\n", "  source: \"штангенциркуль, три точки\"\n")
    (workdir / "params" / "base.yaml").write_text(text, encoding="utf-8")

    after = Snapshot.create(Project.open(workdir), "после обмера")
    d = diff(before, after)
    ch = next(p for p in d.params if p.name == "LIP_D")
    assert ch.kind == "provenance" and ch.measured_now
    assert "измеренным" in ch.line()
    assert after.print_ready is True, "без незамеренного проект готов к печати"


def test_new_and_removed_checks_are_tracked(pr, workdir):
    before = Snapshot.create(pr, "было")
    y = workdir / "checks" / "checks.yaml"
    y.write_text(y.read_text(encoding="utf-8")
                 + '\n- id: крышка.нависания_строже\n  use: overhang\n'
                   '  args: {part: "@крышка", limit_deg: 40.0}\n', encoding="utf-8")
    after = Snapshot.create(Project.open(workdir), "стало")
    d = diff(before, after)
    new = d.transitions("new")
    # Своя overhang перебила базовую «крышка.нависания» — та ушла.
    assert [c.id for c in new] == ["крышка.нависания_строже"]
    assert [c.id for c in d.transitions("removed")] == ["крышка.нависания"]


def test_diff_serialises(pair):
    d = diff(*pair).as_dict()
    assert d["regression"] is True
    assert any(c["transition"] == "broken" for c in d["checks"])


def test_transition_line_keeps_the_verdict(pair):
    """Величина не объясняет отказ: у `escape` он бывает двух родов.

    На реальных данных v3 направление -Z падало по заклиниванию при зацепе
    20.7 мм³, а в v4 проходило при 8.65. Без указания причины строка
    «20.7 → 8.65» читается как ухудшение, хотя это починка.
    """
    from datum.doc.diff import diff

    d = diff(*pair)
    broken = next(c for c in d.broken if c.id == "крышка.зацеп.+Z")
    reason = broken.reason()
    assert reason and ("НЕ ДЕРЖИТ" in reason or "КЛИНИТ" in reason), reason
    assert "стало:" in broken.line()

    back = diff(pair[1], pair[0])
    fixed = next(c for c in back.fixed if c.id == "крышка.зацеп.+Z")
    assert "было:" in fixed.line(), "у починки показываем, что было не так"


def test_a_parameter_change_is_shown_in_its_own_unit():
    """В снимке лежит канон (мм), а единица — та, в которой вводили. Раньше
    канон печатался под ярлыком единицы ввода: «H: 5.08 → 7.62 in»."""
    from datum.doc.diff import ParamChange

    line = ParamChange("H", "changed", before=5.08, after=7.62, unit="in").line()
    assert "0.2 → 0.3 in" in line, line
    assert ParamChange("W", "changed", before=10, after=12, unit="mm").line().count("10 → 12 mm")


# ------------------------------------------------ ссылка на снимок
def test_a_tag_is_matched_exactly_not_as_a_substring(tmp_path):
    """Теги `v1` и `v12`: запрос `v1` раньше находил `v12`, и diff сравнивал
    снимок сам с собой, отвечая «без изменений»."""
    from datum.doc.snapshot import pick_snapshot

    files = [tmp_path / "2026-01-01T00-00-00Z__v1.json",
             tmp_path / "2026-01-02T00-00-00Z__v12.json",
             tmp_path / "2026-01-03T00-00-00Z__rev-2.json"]
    assert pick_snapshot(files, "v1") == files[0]
    assert pick_snapshot(files, "v12") == files[1]
    assert pick_snapshot(files, "rev-2") == files[2]
    assert pick_snapshot(files, "-1") == files[2]
    with pytest.raises(KeyError, match="нет снимка"):
        pick_snapshot(files, "v3")
    with pytest.raises(KeyError, match="№9"):
        pick_snapshot(files, "9")


def test_a_partial_reference_must_be_unambiguous(tmp_path):
    from datum.doc.snapshot import pick_snapshot

    files = [tmp_path / "2026-01-01T00-00-00Z__хорошая.json",
             tmp_path / "2026-01-02T00-00-00Z__хорошая-правка.json"]
    assert pick_snapshot(files, "правка") == files[1]
    with pytest.raises(KeyError, match="нескольким"):
        pick_snapshot(files, "01-0")


def test_saving_never_overwrites_a_snapshot_of_the_same_second(tmp_path):
    from datum.doc.snapshot import Snapshot

    s = Snapshot(tag="v1", created="2026-01-01T00:00:00+00:00", project="проба")
    a, b = s.save(tmp_path), s.save(tmp_path)
    assert a != b and a.is_file() and b.is_file()
    assert not list((tmp_path / "snapshots").glob("*.tmp"))


def test_the_diff_json_carries_values_in_their_own_unit():
    """Окно печатает значение рядом с единицей из того же словаря — значит и
    число там обязано быть в этой единице, а не в каноне."""
    from datum.doc.diff import Diff, ParamChange
    from datum.doc.snapshot import Snapshot

    a = Snapshot(tag="a", created="2026-01-01T00:00:00+00:00", project="p")
    d = Diff(a=a, b=a, params=(ParamChange("H", "changed", before=5.08, after=7.62, unit="in"),))
    row = d.as_dict()["params"][0]
    assert row["before"] == pytest.approx(0.2) and row["after"] == pytest.approx(0.3)
    assert row["before_canonical"] == pytest.approx(5.08)
