"""Сквозная проверка MCP-сервера через настоящий stdio-клиент.

Это дверь, через которую в Datum ходит системный Claude Code, поэтому она
проверяется целиком: запуск процесса, рукопожатие, перечень инструментов,
вызовы, обработка ошибок.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = Path(__file__).resolve().parents[3]
VENV_PY = REPO / ".venv" / "bin" / "python"
PY_ROOT = REPO / "py"
LATCH = REPO / "examples" / "latch"

pytestmark = pytest.mark.asyncio


def _params() -> StdioServerParameters:
    return StdioServerParameters(
        command=str(VENV_PY), args=["-m", "datum.cli", "mcp", "--stdio"],
        env={"PYTHONPATH": str(PY_ROOT), "PATH": "/usr/bin:/bin"})


async def _call(name: str, args: dict) -> tuple[bool, dict | str]:
    async with stdio_client(_params()) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool(name, args)
            text = res.content[0].text if res.content else ""
            try:
                return res.is_error, json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return res.is_error, text


# ------------------------------------------------------------------ рукопожатие
async def test_handshake_and_tool_listing():
    async with stdio_client(_params()) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            assert init.server_info.name == "datum"
            assert init.instructions and "escape" in init.instructions, \
                "инструкции обязаны нести главное правило про зацеп"
            names = {t.name for t in (await s.list_tools()).tools}
            assert {"datum_status", "project_info", "params_list", "part_build",
                    "check_run", "check_list", "sweep_assumed", "mesh_diagnose",
                    "mesh_repair", "measure_parts", "section", "occupancy_map",
                    "snapshot_create", "snapshot_list", "snapshot_diff"} <= names


async def test_every_tool_has_a_description():
    async with stdio_client(_params()) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for t in (await s.list_tools()).tools:
                assert t.description and len(t.description) > 30, t.name


# ------------------------------------------------------------------ проект
async def test_status():
    err, d = await _call("datum_status", {})
    assert not err and d["datum"] == "0.1.0"
    assert "escape" in d["checks_available"]


async def test_project_info():
    err, d = await _call("project_info", {"project": str(LATCH)})
    assert not err and d["name"] == "latch"
    assert d["parts"] == ["корпус", "крышка"]
    assert d["params"]["print_ready"] is False


async def test_params_list_can_filter_unknowns():
    err, d = await _call("params_list", {"project": str(LATCH), "only_unknown": True})
    # Незамеренного в проекте два: глубина губки и модуль упругости печатного
    # PLA. Второй появился вместе с проверкой усилия вставки — её число прямо
    # от него зависит, а мерен он не был.
    # MAT_E — модуль из каталога материала (модуль при изгибе Elegoo вместо
    # растяжения): тоже допущение, и проект его видит, хотя мерит своим E_PLA.
    assert not err and set(d["params"]) == {"LIP_D", "E_PLA", "FRICTION", "POISSON_PLA",
                                            "MAT_E"}
    assert all(p["provenance"] == "assumed" for p in d["params"].values())


async def test_derived_params_carry_the_number_that_goes_into_geometry():
    # У производного своё поле — формула; число, которое реально идёт в
    # геометрию, раньше не отдавалось нигде, и окно показывало «5 * NOZZLE».
    err, d = await _call("params_list", {"project": str(LATCH)})
    assert not err
    wall = d["params"]["WALL"]
    assert wall["expr"] == "5 * NOZZLE" and wall["resolved"] == pytest.approx(2.0)
    assert "resolved" not in d["params"]["GAP"]

async def test_part_build():
    err, d = await _call("part_build", {"project": str(LATCH)})
    assert not err and d["ok"]
    assert d["parts"]["крышка"]["topology"]["bodies"] == 1


async def test_check_run_reports_the_table():
    err, d = await _call("check_run", {"project": str(LATCH)})
    assert not err and d["ok"] and d["n_fail"] == 0
    assert "ИТОГ: OK" in d["report"]


async def test_sweep_tells_what_to_measure():
    err, d = await _call("sweep_assumed", {"project": str(LATCH)})
    # LIP_D решает сам по себе; модуль и трение — вместе (усилие вставки уходит
    # внутрь вилки у жёсткого PLA при большом трении); Пуассон — ни так, ни так.
    assert not err and d["critical"] == ["E_PLA", "FRICTION", "LIP_D"]
    # MAT_E из каталога материала не читает ни одна проверка latch — мерить незачем.
    assert sorted(d["harmless"]) == ["MAT_E", "POISSON_PLA"]
    assert "ИЗМЕРИТЬ" in d["report"] and "СОВМЕСТНО" in d["report"]


async def test_check_list_describes_the_catalogue():
    # Агенту каталог приходит текстом: тот же каталог словарём был его копией
    # (65.8 тыс. байт на вызов — аудит 2026-11-21); словарь — у окна и `--json`.
    err, d = await _call("check_list", {})
    assert not err and "checks" not in d
    assert "escape" in d["report"] and "аргументы: part, holder" in d["report"]
    err, d = await _call("check_list", {"names": ["escape"]})
    assert not err and "snap_insertion" not in d["report"] and "принимает на веру" in d["report"]


# ------------------------------------------------------------------ замеры
async def test_measure_parts():
    err, d = await _call("measure_parts",
                         {"project": str(LATCH), "a": "корпус", "b": "крышка"})
    assert not err
    assert d["intersection_mm3"] == pytest.approx(0.0, abs=1e-6)
    assert d["min_gap_mm"] == pytest.approx(0.3, abs=0.01)


async def test_section_shows_what_is_hidden_inside():
    err, d = await _call("section", {"project": str(LATCH), "part": "корпус",
                                     "plane": "xy", "at": 10.0})
    assert not err and not d["empty"]
    assert d["holes"] == 1, "на высоте кармана сечение корпуса — рамка с дырой"



async def test_section_draws_a_picture_on_request():
    """Посмотреть внутрь надо ДО провала, а картинка была только у провалов."""
    err, d = await _call("section", {"project": str(LATCH), "part": "корпус",
                                     "plane": "xz", "at": 20.0, "image": True})
    assert not err
    png = LATCH / d["image"]["path"]
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    # Адрес `/obj/…` — окну; агент читает файл по пути.
    assert "url" not in d["image"] and d["image"]["path"] in d["report"]


async def test_part_view_draws_the_part_in_its_print_pose():
    err, d = await _call("part_view", {"project": str(LATCH), "part": "крышка",
                                       "pose": "print"})
    assert not err and d["print_up"] == "+Z"
    png = LATCH / d["image"]["path"]
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert "в позе печати" in d["report"]

async def test_section_rejects_a_bad_plane():
    err, d = await _call("section", {"project": str(LATCH), "part": "корпус",
                                     "plane": "боком", "at": 1.0})
    # Набор плоскостей — в схеме инструмента, и неверную отвергает сам слой
    # MCP, называя допустимые, ещё до операции.
    assert err and "xy" in str(d) and "yz" in str(d)


async def test_occupancy_map_is_readable():
    err, d = await _call("occupancy_map",
                         {"project": str(LATCH), "part": "корпус", "plane": "xz",
                          "at": 20.0, "u_from": 0, "u_to": 60, "v_from": 0,
                          "v_to": 20, "step": 2.0})
    assert not err and "#" in d["map"] and "." in d["map"]


# ------------------------------------------------------------------ чужие меши
async def test_mesh_diagnose_and_repair(tmp_path):
    import trimesh
    from manifold3d import Manifold as M

    from datum.geom.convert import to_trimesh

    stl = tmp_path / "void.stl"
    to_trimesh(M.cube((10, 10, 10)) - M.cube((4, 4, 4)).translate((3, 3, 3))).export(stl)

    err, d = await _call("mesh_diagnose", {"path": str(stl)})
    assert not err and d["sealed_voids"] == 1

    out = tmp_path / "fixed.stl"
    err, d = await _call("mesh_repair", {"path": str(stl), "out": str(out)})
    assert not err and d["written"] == str(out) and out.is_file()


# ------------------------------------------------------------------ ошибки
async def test_missing_project_is_reported_not_crashed():
    err, d = await _call("project_info", {"project": "/нет/такого"})
    assert "error" in d and "нет каталога" in d["error"]


async def test_missing_file_is_reported_not_crashed():
    err, d = await _call("mesh_diagnose", {"path": "/nope/missing.stl"})
    assert "error" in d


# ------------------------------------------------------------------ версии
async def test_snapshot_and_diff_through_mcp(tmp_path):
    """Агент должен уметь снять две версии и увидеть регрессию."""
    import shutil

    work = tmp_path / "latch"
    shutil.copytree(LATCH, work)

    err, a = await _call("snapshot_create", {"project": str(work), "tag": "хорошая"})
    assert not err and a["print_ready"] is False, a

    err, b = await _call("snapshot_create",
                         {"project": str(work), "tag": "плохая",
                          "overrides": {"LIP_D": 0.3}})
    assert not err, b

    err, lst = await _call("snapshot_list", {"project": str(work)})
    assert not err and len(lst["snapshots"]) == 2

    err, d = await _call("snapshot_diff", {"project": str(work)})
    assert not err and d["regression"] is True
    broken = {c["id"] for c in d["checks"] if c["transition"] == "broken"}
    assert broken == {"крышка.зацеп.+Z", "губка.против_зазора", "крышка.защёлка"}
    assert "РЕГРЕССИЯ" in d["report"]


async def test_snapshot_diff_needs_two(tmp_path):
    import shutil

    work = tmp_path / "latch2"
    shutil.copytree(LATCH, work)
    await _call("snapshot_create", {"project": str(work), "tag": "одна"})
    err, d = await _call("snapshot_diff", {"project": str(work)})
    assert "error" in d and "меньше двух" in d["error"]


async def test_fem_answers_reach_the_agent_over_mcp():
    """Найдено обзором: `dofs` было numpy.int32, MCP не превращал ответ в JSON,
    и `fem_verify` падал «UnexpectedToolError» уже ПОСЛЕ расчёта."""
    err, d = await _call("fem_verify", {"modulus": 2000})
    assert not err, d
    assert d["ok"] and all(isinstance(c["dofs"], int) for c in d["cases"])


async def test_op_results_are_plain_python():   # pytestmark модуля — asyncio
    import numpy as np

    from datum.tools.registry import _plain

    out = _plain({"n": np.int32(675), "x": np.float64(1.5), "v": np.arange(3),
                  "nested": [{"m": np.int64(2)}], "t": (np.float32(0.5), 1)})
    assert out == {"n": 675, "x": 1.5, "v": [0, 1, 2], "nested": [{"m": 2}], "t": [0.5, 1]}
    assert type(out["n"]) is int and type(out["x"]) is float
