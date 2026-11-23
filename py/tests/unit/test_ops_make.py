"""Пробный образец посадки и ведомость изделия."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from datum.tools import ops, ops_make

LATCH = Path(__file__).resolve().parents[3] / "examples" / "latch"


@pytest.fixture()
def latch(tmp_path):
    root = tmp_path / "latch"
    shutil.copytree(LATCH, root, ignore=shutil.ignore_patterns(".datum", "output"))
    ops.clear_project_cache()
    yield root
    ops.clear_project_cache()


def _run(coro):
    return asyncio.run(coro)


def test_coupon_cuts_the_joint_out_of_both_parts(latch):
    """«Какой файл печатать первым для тестов?» — узел посадки, а не всё изделие."""
    from datum.io.read3mf import read_3mf

    out = _run(ops_make.coupon(str(latch), ["корпус", "крышка"], [0, 0, 8, 20, 40, 20],
                               name="угол"))
    assert "error" not in out, out
    assert set(out["parts"]) == {"корпус", "крышка"}
    for name, piece in out["parts"].items():
        path = latch / piece["file"]
        assert path.is_file() and path.parent.name == "coupons"
        assert 0.05 < piece["share"] < 0.6, "кусок, а не вся деталь"
        verts, faces = read_3mf(path)
        assert len(faces) > 0 and float(verts[:, 2].min()) == pytest.approx(0.0, abs=1e-6), \
            "образец лежит на столе"
        assert max(piece["extents_mm"]) <= 40.0 + 1e-6
    # Выгрузка самих деталей не тронута, и хук критика образцов не видит.
    assert not list((latch / "output").glob("*.3mf"))
    assert "print_log" in out["report"] and "жёсткость" in out["report"]


def test_coupon_refusals_write_nothing(latch):
    cases = [(["корпус"], [0, 0, 0, 1, 40, 20], "тоньше"),
             (["корпус"], [0, 0, 0, 10, 10], "шесть чисел"),
             (["нет"], [0, 0, 0, 10, 10, 10], "нет таких деталей"),
             ([], [0, 0, 0, 10, 10, 10], "ни одной детали"),
             (["крышка"], [0, 0, 0, 20, 20, 5], "не задевает деталь")]
    for parts, box, words in cases:
        out = _run(ops_make.coupon(str(latch), parts, box))
        assert words in out.get("error", ""), (parts, box, out)
    assert not (latch / "output" / "coupons").exists()


def test_bom_counts_plastic_by_print_settings_and_lists_the_bought(latch):
    (latch / "parts" / "extra.py").write_text(
        "from datum.build import centre_on_bed, part\n"
        "from datum.geom import Expect, box, compose, cyl_z\n\n\n"
        "@part('винт_M3', printed=False)\n"
        "def screw():\n"
        "    return cyl_z(3, 0, 16, 100, 100)\n\n\n"
        "@part('ножка_левая')\n"
        "def left():\n"
        "    return centre_on_bed(box(0, 10, 0, 10, 0, 20), dx=-40)\n\n\n"
        "@part('ножка_правая')\n"
        "def right():\n"
        "    return centre_on_bed(box(0, 10, 0, 10, 0, 20), dx=-40)\n\n\n"
        "@part('витрина', expect=Expect(bodies=None))\n"
        "def show():\n"
        "    return compose([box(100, 110, 100, 110, 0, 5), box(120, 130, 100, 110, 0, 5)])\n",
        encoding="utf-8")
    ops.clear_project_cache()
    out = _run(ops_make.bom(str(latch)))
    assert "error" not in out, out
    assert [b["name"] for b in out["bought"]] == ["винт_M3"]
    legs = next(r for r in out["printed"] if "ножка_левая" in r["names"])
    assert legs["count"] == 2 and legs["names"] == ["ножка_левая", "ножка_правая"]
    assert "×2 (ножка_левая, ножка_правая)" in out["report"]
    total = sum(r["mass_g"] * r["count"] for r in out["printed"] if r["mass_g"] is not None)
    assert out["plastic_g"] == pytest.approx(total, abs=0.1) and total > 10
    assert out["suspect"] == ["витрина"] and "показную сборку" in out["report"]


def test_bom_mass_is_the_part_mass(latch):
    from datum.tools import ops_sim

    listed = {r["names"][0]: r["mass_g"] for r in _run(ops_make.bom(str(latch)))["printed"]}
    ops.clear_project_cache()
    exact = _run(ops_sim.part_mass(str(latch)))["parts"]
    for name, grams in listed.items():
        assert grams == pytest.approx(exact[name]["mass_g"], abs=0.01)
