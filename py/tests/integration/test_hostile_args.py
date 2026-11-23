"""Каждая операция с крайними, но конечными числами: ответ правдив или отказ
объяснён — и ничего не падает, не виснет и не портит проект.

Идея — из cadcraft (`panic_hunt.rs`: каждая команда реестра с враждебными
параметрами обязана вернуть ошибку, а не упасть или зависнуть). Здесь перебор
нашёл не падения, а ЛОЖНЫЕ ответы с кодом 0: «зазор 0.000 мм» у деталей в
0.3 мм друг от друга (`search=1e-300`), площадь тени 0 и габарит nan
(`clearance=1e-300`, `z_to=1e308`), снимок версии с объёмом nan, `budget_mb=-1`,
выбросивший весь кеш, — и сырые `MemoryError` на 934 ГиБ, `std::bad_alloc` и
SIGSEGV мешера, `ZeroDivisionError`, `IndexError`, `LinAlgError`.

Что держит тест — для каждой операции и каждого числового аргумента:

  * ответ — словарь, процесс не убит пределами (не повис, не съел память);
  * в ответе нет nan (бесконечность законна: «запаса нет предела»);
  * отказ объяснён словами — по-русски, а не текстом numpy или GEOS;
  * отказавшая операция, меняющая проект, не тронула его файлов.

Операции перебираются из реестра: новая операция с числовым аргументом
попадёт сюда сама, а без строки в BASELINE (если ей нужны обязательные
аргументы) тест упадёт и скажет, что дописать.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest

import datum.tools.ops  # noqa: F401  — объявление операций
from datum.build.worker import WorkerLimits
from datum.tools.isolate import run_isolated
from datum.tools.registry import OPS

ROOT = Path(__file__).resolve().parents[3]
LATCH = ROOT / "examples" / "latch"
GENERATED = shutil.ignore_patterns(".datum", "snapshots", "__pycache__", "output")

#: Правильный вызов каждой операции с обязательными аргументами: крайнее
#: число подставляется в ОДИН аргумент, остальное остаётся разумным, чтобы
#: вызов дошёл до настоящей работы. "@MESH" — путь к пробному STL.
BASELINE: dict[str, dict] = {
    "part_place": dict(part="крышка", move=[1.0, 0.0, 0.0], rotate=[0.0, 0.0, 10.0]),
    "param_set": dict(name="CASE_H", value=21.0),
    "param_add": dict(name="RIB_T", provenance="chosen", unit="mm", value=1.6),
    "param_edit": dict(name="LIP_D", note="проба"),
    "param_set_many": dict(values={"CASE_H": 21.0}),
    "coupon": dict(parts=["корпус"], box=[0.0, 0.0, 8.0, 20.0, 40.0, 20.0]),
    "check_explain": dict(check="крышка.зацеп.+Z"),
    "param_solve": dict(check="крышка.зацеп.+Z", param="LIP_D"),
    "fem_verify": dict(modulus=2000.0, poisson=0.35),
    "fem_solve": dict(part="крышка", modulus=2000.0, force=1.0, fixed="x_min",
                      load="x_max", along="-Z"),
    "snapshot_create": dict(tag="проба", overrides={"CASE_H": 21.0}),
    "measure_parts": dict(a="крышка", b="корпус"),
    "measure_probe": dict(part="крышка", point=[30.0, 20.0, 40.0],
                          direction=[0.0, 0.0, -1.0]),
    "section": dict(part="крышка", plane="xy", at=15.0),
    "part_view": dict(part="крышка"),
    "footprint": dict(part="крышка"),
    "occupancy_map": dict(part="крышка", plane="xy", at=15.0, u_from=0.0, u_to=60.0,
                          v_from=0.0, v_to=40.0),
    "mesh_repair": dict(path="@MESH", level="remesh"),
    "window_report": dict(state={"camera": {"fov": 40.0}}),
    # у latch механизма нет: правильное «ребро» доходит до отказа «нет механизма»,
    # крайнее — отсекается до работы (мешер на 1e308 падал SIGSEGV)
    "sections": dict(part="крышка", plane="xy", at=[15.0]),
    "section_scene": dict(plane="xy", at=15.0),
    # «int» в слове footprint из пояснения к sign: перебор подставляет число в
    # строковый аргумент — ответ обязан быть отказом словами, а не записью урока
    "print_log": dict(outcome="заметка", text="проба", part="крышка"),
    "sim_run": dict(mechanism="нет", scenario="нет"),
    "sim_stress": dict(mechanism="нет", scenario="нет"),
}

FLOATS = [1e308, -1e308, 1e-300, 0.0, -1.0, 1e9]
INTS = [10 ** 9, -1, 0]
LISTS = [[], [1e308] * 3, [0.0] * 3, [1.0] * 7, [-1e308] * 6,
         [-1e308, -1e308, -1e308, 1e308, 1e308, 1e308], [0.0, 0.0, 0.0, 1e-300, 1e-300, 1e-300]]
DICTS = [{"CASE_H": 1e308}, {"CASE_H": -1e308}, {"CASE_H": 1e-300}, {"k": 1e308}]

#: Сколько можно одному вызову: настоящая работа на latch — секунды.
LIMITS = WorkerLimits(cpu_seconds=60, wall_seconds=90)

#: Файлы проекта, которые не вправе меняться у ОТКАЗАВШЕЙ операции.
SOURCES = ("datum.toml", "params", "parts", "checks", "assembly.yaml", "placement.yaml",
           "imported", "lib", "fonts")

CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)


def _kind(arg) -> str | None:
    text = str(arg.annotation)
    if "Literal" in text or arg.name == "project":
        return None
    if "list[float]" in text:
        return "list"
    if "dict" in text and "str]" not in text.split("dict")[1][:12]:
        return "dict"
    if "float" in text:
        return "float"
    if "int" in text:
        return "int"
    return None


def _cases():
    for spec in sorted(OPS.values(), key=lambda s: s.name):
        for arg in spec.args:
            kind = _kind(arg)
            values = {"float": FLOATS, "int": INTS, "list": LISTS, "dict": DICTS}.get(kind or "")
            for value in values or ():
                yield pytest.param(spec.name, arg.name, value,
                                   id=f"{spec.name}.{arg.name}={value!r}"[:80])


def _fingerprint(root: Path) -> str:
    h = hashlib.sha256()
    for name in SOURCES:
        path = root / name
        files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file()) \
            if path.is_dir() else []
        for f in files:
            h.update(str(f.relative_to(root)).encode("utf-8") + b"\0" + f.read_bytes())
    return h.hexdigest()


@pytest.fixture(scope="module")
def warm(tmp_path_factory) -> Path:
    """latch, собранный однажды: каждый вызов получает свою копию с кешем."""
    from datum.doc.project import Project
    from datum.geom import box

    base = tmp_path_factory.mktemp("враждебные") / "latch"
    shutil.copytree(LATCH, base, ignore=GENERATED)
    assert all(r.ok for r in Project.open(base).build().values())
    box(0, 10, 0, 10, 0, 10).to_trimesh().export(base.parent / "кубик.stl")
    return base


def test_every_operation_with_required_arguments_has_a_baseline():
    missing = sorted(
        spec.name for spec in OPS.values()
        if any(_kind(a) for a in spec.args)
        and any(a.required and a.name != "project" for a in spec.args)
        and spec.name not in BASELINE)
    assert not missing, f"допишите правильный вызов в BASELINE для: {missing}"


@pytest.mark.parametrize("name, arg, value", list(_cases()))
def test_extreme_numbers_get_a_true_answer_or_an_explained_refusal(warm, tmp_path,
                                                                   name, arg, value):
    spec = OPS[name]
    project = tmp_path / "latch"
    shutil.copytree(warm, project, symlinks=True)
    kwargs = dict(BASELINE.get(name, {}))
    if any(a.name == "project" for a in spec.args):
        kwargs["project"] = str(project)
    if kwargs.get("path") == "@MESH":
        shutil.copy(warm.parent / "кубик.stl", tmp_path / "кубик.stl")
        kwargs["path"] = str(tmp_path / "кубик.stl")
    kwargs[arg] = value
    before = _fingerprint(project)

    out = run_isolated(spec.fn, kwargs, limits=LIMITS, headroom_mb=spec.memory_headroom_mb)

    assert isinstance(out, dict)
    assert "worker_status" not in out, f"процесс убит пределами: {out.get('error')}"
    try:
        json.dumps(out, allow_nan=False, default=str)
    except ValueError:
        pytest.fail(f"в ответе nan: {str(out.get('report'))[:300]}")
    error = str(out.get("error") or "")
    if error:
        assert CYRILLIC.search(error.split(":", 1)[-1]), f"отказ не объяснён: {error[:300]}"
        if spec.danger != "safe":
            assert _fingerprint(project) == before, "отказ, но проект изменён"
