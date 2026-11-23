"""Общая библиотека покупного: замер, сделанный однажды, — для всех проектов.

Мотор 5840-31ZY обмерен в проекте пресса и понадобился в руке; агент
переписывал его числа из чужого `params/base.yaml` руками (аудит 2026-11-21).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from datum.doc import components as C
from datum.doc.params import ParamRegistry
from datum.doc.params_edit import param_text
from datum.tools import ops, ops_components



def cube_project(root: Path) -> Path:
    """Второй проект с кубиком — тот же, что даёт фикстура `cube`. Берётся по
    пути к файлу: модулей `conftest` в прогоне несколько (unit, integration)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_unit_conftest", Path(__file__).with_name("conftest.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.cube_project(root)


MOTOR = '''
# ===== мотор 5840-31ZY: замер заказчика =====
MOTOR_GB_W:
  value: 39.8
  unit: mm
  provenance: measured
  # фото штангенциркуля в чате от 16 октября
  source: "штангенциркуль 2026-10-16: 39.83"
MOTOR_SHAFT_D: {value: 8.0, unit: mm, provenance: standard, ref: "паспорт: вал D8"}
MOTOR_BORE:
  provenance: derived
  expr: "MOTOR_SHAFT_D + FIT"
  unit: mm
FIT: {value: 0.2, unit: mm, provenance: assumed, range: [0.1, 0.4], reason: "посадка вала"}

# ===== дальше — своё проекта =====
OTHER: {value: 1.0, unit: mm, provenance: chosen, reason: "не мотор"}
'''

MOTOR_LIB = '''"""Макет мотора 5840-31ZY."""
from datum.build import P
from datum.geom import box


def motor_body():
    return box(0, P.MOTOR_GB_W, 0, P.MOTOR_GB_W, 0, P.MOTOR_LEN)
'''


@pytest.fixture()
def library(tmp_path, monkeypatch):
    monkeypatch.setenv("DATUM_DATA_HOME", str(tmp_path / "xdg" / "datum"))
    ops.clear_project_cache()
    yield tmp_path / "xdg" / "datum" / "components"
    ops.clear_project_cache()


@pytest.fixture()
def press(cube):
    """Проект, в котором мотор обмерен."""
    path = cube / "params" / "base.yaml"
    path.write_text(path.read_text("utf-8") + MOTOR, encoding="utf-8")
    (cube / "lib").mkdir(exist_ok=True)
    (cube / "lib" / "motor.py").write_text(MOTOR_LIB, encoding="utf-8")
    return cube


def _run(coro):
    return asyncio.run(coro)


def _save(root, name="5840-31ZY", **kw):
    return _run(ops_components.component_save(str(root), name, **kw))


def _use(root, name="5840-31ZY"):
    return _run(ops_components.component_use(str(root), name))


# ------------------------------------------------------------------- текст
def test_a_parameter_travels_as_its_own_text_with_its_comments():
    block = param_text(MOTOR, "MOTOR_GB_W")
    assert block.startswith("MOTOR_GB_W:\n") and "# фото штангенциркуля" in block
    assert block.rstrip().endswith('source: "штангенциркуль 2026-10-16: 39.83"')
    assert param_text(MOTOR, "MOTOR_SHAFT_D").count("\n") == 1
    # Заголовок СЛЕДУЮЩЕГО раздела к параметру не относится.
    assert "дальше" not in param_text(MOTOR, "FIT")
    assert "expr" in param_text(MOTOR, "MOTOR_BORE") and "FIT:" not in param_text(MOTOR, "MOTOR_BORE")


# -------------------------------------------------------------- сохранение
def test_saving_takes_parameters_by_prefix_and_names_with_their_files(press, library):
    res = _save(press, prefix="MOTOR_", params=["FIT"], files=["lib/motor.py"],
                title="мотор-редуктор 24 В", note="вал мерил сам")
    assert "error" not in res, res
    assert res["params"] == ["FIT", "MOTOR_GB_W", "MOTOR_SHAFT_D", "MOTOR_BORE"]
    assert res["assumed"] == ["FIT"] and "УГАДАННЫЕ" in res["report"]
    assert res["unbound"] == ["MOTOR_LEN"], "скрипт читает параметр, которого в компоненте нет"
    folder = library / "5840-31ZY"
    assert (folder / "files" / "lib" / "motor.py").read_text("utf-8") == MOTOR_LIB
    saved = ParamRegistry.loads((folder / "params.yaml").read_text("utf-8"))
    assert saved["MOTOR_GB_W"].provenance == "measured"
    assert "штангенциркуль" in (folder / "params.yaml").read_text("utf-8")
    listed = _run(ops_components.component_list(str(press)))
    assert [c["name"] for c in listed["components"]] == ["5840-31ZY"]
    assert "мотор-редуктор 24 В" in listed["report"] and "кубик" in listed["report"]
    assert not _run(ops_components.component_list(str(press), "серва"))["components"]


def test_a_formula_reading_outside_the_component_is_refused(press, library):
    res = _save(press, prefix="MOTOR_")
    assert "MOTOR_BORE ← FIT" in res["error"] and not library.exists()


@pytest.mark.parametrize("kw, words", [
    (dict(params=["НЕТ_ТАКОГО"]), "нет в файле"),
    (dict(prefix="ZZZ_"), "нет параметров, начинающихся"),
    (dict(), "пустой компонент"),
    (dict(files=["parts/cube.py"]), "только файлы из lib/, data/"),
    (dict(files=["lib/../datum.toml"]), "только файлы из"),
    (dict(files=["lib/нет.py"]), "в проекте нет"),
])
def test_what_cannot_be_saved_is_refused_in_words(press, library, kw, words):
    assert words in _save(press, **kw)["error"]


@pytest.mark.parametrize("name", ["../x", "a/b", ".скрытый", "", "x" * 80, "-минус"])
def test_a_name_that_is_not_a_name_is_refused(press, library, name):
    assert "имя компонента" in _save(press, name, params=["FIT"])["error"]


def test_an_existing_component_is_replaced_only_on_request(press, library):
    assert "error" not in _save(press, params=["FIT"])
    assert "уже есть" in _save(press, params=["FIT"])["error"]
    res = _save(press, params=["FIT", "MOTOR_SHAFT_D"], replace=True)
    assert res["params"] == ["FIT", "MOTOR_SHAFT_D"]
    assert sorted(p.name for p in library.iterdir()) == ["5840-31ZY"], "временных папок не осталось"


# ---------------------------------------------------------------- в проект
def test_a_component_arrives_as_a_marked_copy_and_the_project_still_resolves(
        press, library, tmp_path):
    _save(press, prefix="MOTOR_", params=["FIT"], files=["lib/motor.py"], title="мотор")
    arm = cube_project(tmp_path / "рука")
    res = _use(arm)
    assert res["added"] == ["FIT", "MOTOR_GB_W", "MOTOR_SHAFT_D", "MOTOR_BORE"]
    assert res["files"] == ["lib/motor.py"]
    text = (arm / "params" / "base.yaml").read_text("utf-8")
    assert "# ── из библиотеки компонентов: 5840-31ZY — мотор (сохранён" in text
    assert "проекта «кубик»" in text and "# фото штангенциркуля" in text
    reg = ParamRegistry.loads(text)
    assert reg["MOTOR_GB_W"].provenance == "measured" and reg["FIT"].provenance == "assumed"
    assert reg.resolve()["MOTOR_BORE"] == pytest.approx(8.2)
    assert (arm / "lib" / "motor.py").read_text("utf-8") == MOTOR_LIB
    # Второй раз — ничего нового: всё на месте.
    again = _use(arm)
    assert not again["added"] and not again["files"] and "ничего не записано" in again["report"]
    assert (arm / "params" / "base.yaml").read_text("utf-8") == text


def test_a_parameter_the_project_measured_itself_is_left_alone_and_named(
        press, library, tmp_path):
    _save(press, params=["MOTOR_GB_W", "MOTOR_SHAFT_D"])
    arm = cube_project(tmp_path / "рука")
    path = arm / "params" / "base.yaml"
    path.write_text(path.read_text("utf-8") + '\nMOTOR_GB_W: {value: 40.1, unit: mm, '
                    'provenance: measured, source: "свой замер"}\n', encoding="utf-8")
    res = _use(arm)
    assert res["added"] == ["MOTOR_SHAFT_D"]
    assert res["differ"] == [{"param": "MOTOR_GB_W", "project": "40.1 mm", "library": "39.8 mm"}]
    assert "проект НЕ тронут" in res["report"]
    assert ParamRegistry.loads(path.read_text("utf-8")).resolve()["MOTOR_GB_W"] == pytest.approx(40.1)


def test_a_different_file_of_the_same_name_stops_everything(press, library, tmp_path):
    _save(press, params=["MOTOR_SHAFT_D"], files=["lib/motor.py"])
    arm = cube_project(tmp_path / "рука")
    (arm / "lib").mkdir(exist_ok=True)
    (arm / "lib" / "motor.py").write_text("# свой мотор\n", encoding="utf-8")
    before = (arm / "params" / "base.yaml").read_text("utf-8")
    res = _use(arm)
    assert "не затирает" in res["error"]
    assert (arm / "params" / "base.yaml").read_text("utf-8") == before, "не записано ничего"
    assert (arm / "lib" / "motor.py").read_text("utf-8") == "# свой мотор\n"


def test_a_component_the_registry_rejects_writes_nothing(press, library, tmp_path):
    """Имя параметра компонента занято в проекте формулой — круг; реестр откажет."""
    _save(press, prefix="MOTOR_", params=["FIT"], files=["lib/motor.py"])
    # Испортить библиотечную запись: формула читает то, чего нигде нет.
    lib_params = library / "5840-31ZY" / "params.yaml"
    lib_params.write_text(lib_params.read_text("utf-8").replace(
        "MOTOR_SHAFT_D + FIT", "MOTOR_SHAFT_D + НЕТ_ТАКОГО"), encoding="utf-8")
    arm = cube_project(tmp_path / "рука")
    before = (arm / "params" / "base.yaml").read_text("utf-8")
    res = _use(arm)
    assert "error" in res
    assert (arm / "params" / "base.yaml").read_text("utf-8") == before
    assert not (arm / "lib" / "motor.py").exists(), "файлы не легли раньше параметров"


def test_an_unknown_component_names_what_there_is(press, library, tmp_path):
    _save(press, params=["FIT"])
    assert "есть: 5840-31ZY" in _use(cube_project(tmp_path / "рука"), "MG996R")["error"]


def test_a_library_record_with_a_path_outside_is_refused(press, library, tmp_path):
    _save(press, params=["FIT"], files=["lib/motor.py"])
    meta = library / "5840-31ZY" / "component.json"
    meta.write_text(meta.read_text("utf-8").replace("lib/motor.py", "../../evil.py"),
                    encoding="utf-8")
    arm = cube_project(tmp_path / "рука")
    assert "не кладётся" in _use(arm)["error"]
    assert not (tmp_path / "evil.py").exists()


def test_the_library_lives_under_the_data_home(monkeypatch, tmp_path):
    monkeypatch.delenv("DATUM_DATA_HOME", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert C.library_root() == tmp_path / "datum" / "components"
    monkeypatch.delenv("XDG_DATA_HOME")
    assert C.library_root() == Path.home() / ".local" / "share" / "datum" / "components"
    # Свой каталог общих данных — главнее: так их держат в стороне тесты.
    monkeypatch.setenv("DATUM_DATA_HOME", str(tmp_path / "своё"))
    assert C.library_root() == tmp_path / "своё" / "components"
