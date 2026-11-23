"""Разбор тела G-code: острова в воздухе, нависающие стенки, мосты, первый слой.

Слайсер — единственный, кто знает, как деталь будет напечатана. «Лепесток
висит в воздухе» в Gear Cube нашёл человек, глядя в окно слайсера: Datum читал
у G-code только подвал (аудит 2026-11-21, §7.3).
"""

from __future__ import annotations

import asyncio
import math
import os
import time
from pathlib import Path

import pytest

from datum.io.gcode_report import ISLAND_MIN_MM2, analyse
from datum.io.slicer import SlicerError
from datum.tools import ops, ops_gcode



def _real() -> bool:
    """Есть ли настоящий слайсер — как в test_slicer."""
    from datum.io.slicer import slicer_command, system_dir

    try:
        slicer_command()
    except SlicerError:
        return False
    return (system_dir() / "Elegoo.json").is_file()


WIDTH = 0.4
#: Подача на миллиметр пути — как у линии 0.4×0.2 прутком 1.75.
E_PER_MM = WIDTH * 0.2 / (math.pi * 0.875 ** 2)


class Gcode:
    """Маленький писатель G-code в разметке ElegooSlicer."""

    def __init__(self, relative: bool = True):
        self.lines = ["; HEADER_BLOCK_START", "M83" if relative else "M82",
                      ";TYPE:Custom", "G1 X5 Y5 E3"]
        self.z = 0.0

    def layer(self, height: float = 0.2) -> "Gcode":
        self.z += height
        self.lines += [";LAYER_CHANGE", f";Z:{self.z:g}", f";HEIGHT:{height:g}"]
        return self

    def path(self, kind: str, points, width: float = WIDTH) -> "Gcode":
        self.lines += [f";TYPE:{kind}", f";WIDTH:{width:g}",
                       f"G1 X{points[0][0]:g} Y{points[0][1]:g} F30000"]
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            self.lines.append(f"G1 X{x1:g} Y{y1:g} E{math.hypot(x1 - x0, y1 - y0) * E_PER_MM:.5f}")
        return self

    def square(self, kind: str, x: float, y: float, side: float) -> "Gcode":
        """Квадрат, залитый змейкой с шагом в ширину линии."""
        pts, row, flip = [], y, False
        while row <= y + side + 1e-9:
            ends = [(x, row), (x + side, row)]
            pts += ends[::-1] if flip else ends
            row, flip = row + WIDTH, not flip
        return self.path(kind, pts)

    def write(self, path: Path) -> Path:
        path.write_text("\n".join([*self.lines, "; EXECUTABLE_BLOCK_END", "",
                                   "; filament_density = 1.25"]) + "\n", encoding="utf-8")
        return path


def _tower(layers: int = 5) -> Gcode:
    g = Gcode()
    for _ in range(layers):
        g.layer().square("Inner wall", 100, 100, 10)
    return g


def test_a_plain_tower_has_no_islands(tmp_path):
    r = analyse(_tower().write(tmp_path / "a.gcode"), density=1.25)
    assert r.layers == 5 and r.height_mm == pytest.approx(1.0)
    assert not r.islands and r.overhang_mm == 0 and r.bridge_mm == 0
    # Первый слой: 26 строк по 10 мм и 25 перемычек по 0.4 мм, шириной 0.4.
    assert r.first_layer_mm2 == pytest.approx((26 * 10 + 25 * 0.4) * WIDTH, rel=1e-6)
    assert "островов в воздухе нет" in "\n".join(r.lines())


def test_a_piece_of_a_layer_with_nothing_under_it_is_an_island(tmp_path):
    """Третий слой начинает в стороне кусок, под которым пусто, — как лепесток."""
    g = Gcode()
    for n in range(5):
        g.layer().square("Inner wall", 100, 100, 10)
        if n >= 2:
            g.square("Bridge" if n == 2 else "Inner wall", 120, 100, 3)
    r = analyse(g.write(tmp_path / "a.gcode"), density=1.25)
    assert len(r.islands) == 1, "остров — только первый слой куска: дальше он лежит на себе"
    island = r.islands[0]
    assert island.layer == 3 and island.z == pytest.approx(0.6)
    assert island.centre == pytest.approx((121.5, 101.4), abs=0.3)
    assert island.size[0] == pytest.approx(3.0, abs=0.01)
    text = "\n".join(r.lines(supports_on=False))
    assert "ОСТРОВА В ВОЗДУХЕ — 1" in text and "слой 3 (Z 0.60)" in text
    assert "enable_support=1" in text


def test_a_support_under_the_piece_makes_it_not_an_island(tmp_path):
    g = Gcode()
    for n in range(5):
        g.layer().square("Inner wall", 100, 100, 10)
        if n < 2:
            g.square("Support", 120, 100, 3)
        else:
            g.square("Inner wall", 120, 100, 3)
    r = analyse(g.write(tmp_path / "a.gcode"), density=1.25)
    assert not r.islands
    assert r.support_g > 0 and r.part_g > r.support_g


def test_a_wall_leaning_by_a_fraction_of_a_line_is_not_an_island(tmp_path):
    """Стенка под 45° уходит на слой 0.2 мм за слой — опора у неё есть."""
    g = Gcode()
    for n in range(20):
        g.layer().square("Outer wall", 100 + 0.2 * n, 100, 4)
    assert not analyse(g.write(tmp_path / "a.gcode"), density=1.25).islands


def test_a_speck_is_not_reported(tmp_path):
    g = _tower(2)
    g.layer().square("Inner wall", 100, 100, 10).path("Gap infill", [(130, 100), (130.5, 100)])
    r = analyse(g.write(tmp_path / "a.gcode"), density=1.25)
    assert 0.5 * WIDTH < ISLAND_MIN_MM2 and not r.islands


def test_overhang_walls_and_bridges_are_summed_by_layer_runs(tmp_path):
    g = Gcode()
    for n in range(8):
        g.layer().square("Inner wall", 100, 100, 10)
        if n in (2, 3):
            g.path("Overhang wall", [(100, 100), (110, 100)])
        if n == 6:
            g.path("Overhang wall", [(100, 100), (104, 100)])
            g.path("Bridge", [(100, 105), (107, 105), (107, 105.4), (100, 105.4)])
            g.path("Internal Bridge", [(100, 107), (109, 107)])
    r = analyse(g.write(tmp_path / "a.gcode"), density=1.25)
    assert r.overhang_mm == pytest.approx(24.0)
    assert [(run.first, run.last, round(run.length_mm, 3)) for run in r.overhang_runs] == \
        [(3, 4, 20.0), (7, 7, 4.0)]
    assert r.overhang_runs[0].z0 == pytest.approx(0.4) and r.overhang_runs[0].z1 == pytest.approx(0.8)
    assert r.bridge_mm == pytest.approx(14.4), "внутренний мост — не мост детали"
    assert r.longest_bridge_mm == pytest.approx(7.0) and r.longest_bridge_z == pytest.approx(1.4)
    text = "\n".join(r.lines())
    assert "слои 3–4 (Z 0.40…0.80) 20 мм" in text and "самая длинная прямая 7.0 мм" in text


def test_grams_agree_with_the_tail_reader(tmp_path):
    """Пластик детали здесь и в `slicer.part_grams` — одно число: по нему
    сверяется модель массы."""
    from datum.io.slicer import part_grams

    g = _tower(3)
    g.lines.insert(g.lines.index(";LAYER_CHANGE") + 3, ";TYPE:Brim\n;WIDTH:0.5\nG1 X90 Y90\nG1 X95 Y90 E0.2")
    path = g.write(tmp_path / "a.gcode")
    r = analyse(path, density=1.25)
    assert r.part_g == pytest.approx(part_grams(path, density=1.25), rel=1e-9)
    assert r.brim_mm2 == pytest.approx(5 * 0.5)
    assert r.by_kind["Custom"]["grams"] > 0 and r.by_kind["Brim"]["grams"] > 0


def test_absolute_extrusion_is_refused(tmp_path):
    g = Gcode(relative=False)
    g.lines = g.lines[:2]
    g.layer().square("Inner wall", 100, 100, 5)
    with pytest.raises(SlicerError, match="абсолютная"):
        analyse(g.write(tmp_path / "a.gcode"), density=1.25)


# ------------------------------------------------------------------ операция
@pytest.fixture()
def fresh():
    ops.clear_project_cache()
    yield
    ops.clear_project_cache()


def _report(root, parts=None):
    return asyncio.run(ops_gcode.slice_report(str(root), parts))


def test_the_operation_reads_what_slice_left_and_compares_with_datum(cube, fresh):
    out = cube / "output"
    out.mkdir()
    g = Gcode()
    for n in range(5):
        g.layer().square("Inner wall", 118, 118, 20)
        if n == 3:
            g.path("Bridge", [(118, 128), (138, 128)])
    g.write(out / "кубик_PLA.gcode")
    res = _report(cube)
    part = res["parts"]["кубик"]
    assert part["layers"] == 5 and not part["islands"] and not part["stale"]
    assert part["datum"]["overhang_mm2"] == pytest.approx(0.0)
    # Стакан без нависаний, а слайсер тянет мост 20 мм — это и есть расхождение.
    assert part["mismatches"] and "РАСХОЖДЕНИЕ" in res["report"]
    assert "сверка с Datum" in res["report"]


def test_an_island_without_supports_is_a_warning(cube, fresh):
    out = cube / "output"
    out.mkdir()
    g = Gcode()
    for n in range(4):
        g.layer().square("Inner wall", 118, 118, 20)
        if n >= 2:
            g.square("Inner wall", 150, 118, 3)
    g.write(out / "кубик_PLA.gcode")
    res = _report(cube, ["кубик"])
    assert len(res["parts"]["кубик"]["islands"]) == 1
    assert res["parts"]["кубик"]["warnings"], "остров при выключенных поддержках — предупреждение"
    assert "ляжет в пустоту" in res["report"]


def test_gcode_older_than_the_project_is_named_stale(cube, fresh):
    out = cube / "output"
    out.mkdir()
    path = _tower().write(out / "кубик_PLA.gcode")
    old = time.time() - 3600
    os.utime(path, (old, old))
    res = _report(cube)
    assert res["stale"] == ["кубик"] and "ДО последней правки" in res["report"]


def test_no_gcode_is_a_refusal_in_words(cube, fresh):
    res = _report(cube)
    assert "сначала slice" in res["error"]
    assert "нет таких деталей" in _report(cube, ["крышка"])["error"]


# ---------------------------------------------------------- настоящий слайсер
@pytest.mark.skipif(not _real(), reason="ElegooSlicer не установлен")
def test_a_real_slicer_prints_a_hanging_nub_into_the_air(tmp_path):
    """Стойка с перекладиной, с конца которой вниз свисает зуб, не доходя до
    стола: первый слой зуба слайсер кладёт в пустоту — и с поддержками уже нет."""
    from datum.geom import box, union
    from datum.io.export import on_bed, write_3mf
    from datum.io.slicer import Profiles, choose, slice_model

    shape = union([box(0, 10, 0, 10, 0, 20),            # стойка
                   box(0, 30, 0, 10, 16, 20),           # перекладина
                   box(24, 30, 0, 10, 10, 20)])         # зуб: низ на Z 10, в воздухе
    work = tmp_path / "work"
    work.mkdir()
    model = write_3mf(work / "t.3mf", on_bed(shape, "+Z", (256, 256, 256)), title="зуб")
    p = Profiles()
    c = choose(p, printer_profile="elegoo-cc2-0.4", nozzle=0.4, layer=0.2, material="PLA")
    islands = {}
    for supports in ("0", "1"):
        cut = slice_model(model.path, tmp_path / f"o{supports}" / "t.3mf",
                          tmp_path / f"o{supports}" / "t.gcode", profiles=p, choice=c,
                          triangles=model.triangles, overrides={"enable_support": supports},
                          work=work)
        r = analyse(cut.gcode, density=float(cut.config["filament_density"]))
        assert r.layers == cut.layers
        islands[supports] = r
    bare, held = islands["0"], islands["1"]
    assert len(bare.islands) == 1, [(i.layer, i.area_mm2) for i in bare.islands]
    nub = bare.islands[0]
    assert nub.z == pytest.approx(10.2, abs=0.21), "зуб начинается на Z 10"
    assert nub.size == pytest.approx((6.0, 10.0), abs=0.6)
    assert bare.bridge_mm > 0, "низ перекладины слайсер тянет мостом"
    assert not held.islands and held.support_g > 0
