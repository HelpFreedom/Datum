"""Приёмка порта: дозатор зубной пасты.

Второй проект рабочего процесса CAD, перенесённый в Datum. Смысл теста не в
том, что он «собирается», а в том, что числа СОВПАДАЮТ С ОРИГИНАЛОМ. Разойдись
они — порт стал бы другим устройством, похожим на исходное только названиями.

Эталоны взяты из `<корпус>/projects/toothpaste-dispenser/work/calc.py`
и `check.py`; там они печатались и проверялись одним общим `assert ok`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from datum.doc.project import Project

ROOT = Path(__file__).resolve().parents[3]
DISPENSER = ROOT / "examples" / "dispenser"
GENERATED = shutil.ignore_patterns(".datum", "snapshots", "__pycache__", "output")


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    """Чистая копия: сборка заводит кеш, и грязный пример сбивал бы замеры."""
    target = tmp_path_factory.mktemp("порт") / "dispenser"
    shutil.copytree(DISPENSER, target, ignore=GENERATED)
    return Project.open(target)


@pytest.fixture(scope="module")
def results(project):
    run = project.run_checks()
    return {r.id: r for r in run.results}, run


def test_everything_builds(project):
    built = project.build()
    assert set(built) == {"основание", "пол", "флексура", "шибер"}
    for name, result in built.items():
        assert result.ok, result.line()
        assert result.topology.bodies == 1, f"{name}: тел {result.topology.bodies}"


def test_all_checks_pass(results):
    _, run = results
    assert run.ok, run.report()


# ------------------------------------------------------- сверка с calc.py
@pytest.mark.parametrize("check_id, expected, tol, what", [
    ("флексура.деформация", 0.0140816, 1e-6, "деформация листа с преднатягом, 1.41 %"),
    ("флексура.сила_возврата", 1.889213, 1e-5, "сила флексуры в покое, Н"),
    ("флексура.сила_нажатия", 7.241983, 1e-5, "сила на полном ходе, Н"),
    ("шибер.закрытие", 0.6063987, 1e-6, "сила через кулачок шибера, Н"),
    ("тяга.устойчивость", 21.9327, 1e-3, "критическая сила тяги, Н"),
])
def test_numbers_match_the_original(results, check_id, expected, tol, what):
    by_id, _ = results
    assert check_id in by_id, f"проверки {check_id!r} нет; есть {sorted(by_id)}"
    assert by_id[check_id].value == pytest.approx(expected, abs=tol), what


def test_the_port_reproduces_the_channel_overlap(results):
    """Независимое подтверждение, что геометрия перенесена верно.

    92 % записано в исходном `check.py` как «полное перекрытие канала». Порт
    приходит к тому же числу своим путём — через объём пересечения шибера с
    цилиндром канала.
    """
    by_id, _ = results
    result = by_id["шибер.перекрытие"]
    assert result.value == pytest.approx(0.92, abs=0.01), result.message


# ------------------------------------------------- чего оригинал не видел
def test_the_thinnest_margin_is_the_shutter(results):
    """Оригинал печатал ровное OK. Здесь видно, что сила закрытия шибера —
    0.606 Н при нужных 0.6, то есть полтора процента запаса, и это самое
    опасное место конструкции."""
    _, run = results
    passing = [r for r in run.results if r.status == "pass" and r.margin is not None]
    thinnest = min(passing, key=lambda r: r.margin)
    assert thinnest.id == "шибер.закрытие"
    assert 0 < thinnest.margin < 0.02
    # Впереди него — только предупреждение базовой проверки: у основания в
    # объявленной позе (+Z) нависаний 19.4 % — факт, а не отказ.
    assert [r.id for r in run.results if r.status == "warn"] == ["основание.нависания"]


def test_six_guesses_out_of_nine_decide_the_outcome(project):
    """Главное, чего у оригинала не было вовсе.

    Свойства материала там были константами в начале файла. Здесь они
    параметры с диапазоном, и развёртка показывает: шесть догадок из девяти
    переключают проверку ВНУТРИ СОБСТВЕННОГО заявленного разброса. Значит
    конструкция проходит не с запасом, а потому что догадки поставлены
    посередине.
    """
    sweep = project.sweep()
    deciding = {row.param for row in sweep.rows if row.flips}
    assert {"FRICTION_DEG", "EPS_LIM_PLA", "E_PETG",
            "PRESS_N", "LATERAL_FRAC", "DEVICE_MASS_G"} <= deciding
    # А эти три — нет, и мерить их незачем. Ни по одному, ни попарно.
    assert not {"E_PLA", "POISSON_PLA", "FILL_FRAC"} & set(sweep.critical_params)
    # И ни одно из нечувствительных не держит проект «не готовым к печати».
    assert sweep.blocking_harmless == ()


def test_sweep_names_a_blocking_guess_that_decides_nothing(tmp_path):
    """E_PLA был помечен blocking при «нечувствительно» от той же развёртки:
    проект стоял «не готовым к печати» из-за числа, не меняющего ни одного
    вердикта. Пометку ставит человек — но противоречие обязано быть названо."""
    target = tmp_path / "dispenser"
    shutil.copytree(DISPENSER, target, ignore=GENERATED)
    params = target / "params" / "base.yaml"
    text = params.read_text(encoding="utf-8")
    marker = '  validate_with: test_coupon\n\nPOISSON_PLA:'
    assert marker in text
    params.write_text(text.replace(marker, '  validate_with: test_coupon\n  blocking: true\n\n'
                                           'POISSON_PLA:'), encoding="utf-8")
    sweep = Project.open(target).sweep()
    assert sweep.blocking_harmless == ("E_PLA",)
    assert "Помечены blocking, но ни на что не влияют: E_PLA" in sweep.report()


def test_the_project_is_not_print_ready_while_guesses_block(project):
    """Пока свойства материала не измерены, снимок не готов к печати — и это
    говорится словами, а не подразумевается."""
    summary = project.params.summary()
    assert summary["print_ready"] is False
    assert "EPS_LIM_PLA" in summary["blocking_unknowns"]


def test_shutter_goes_into_the_floor_from_above(project):
    """NOTES: «шибер ставится сдвигом −Z», то есть опускается в паз сверху.

    `assembly.yaml` у порта нет (см. NOTES, почему), и в проверки проекта эта
    пара не входит — утверждение держится здесь, а не на честном слове.
    """
    from datum.checks.assembly import assembly_path

    built = project.build()
    result = assembly_path(built["шибер"].solid, [built["пол"].solid], part_name="шибер")
    assert result.status == "pass", result.message
    assert "ставится сдвигом -Z" in result.message
