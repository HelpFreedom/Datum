"""Настройки печати и масса печатной детали (`datum.fab`).

Масса по слоям сверена с G-code ElegooSlicer на 17 деталях Gear Cube (детали
от 1 г: среднее −0.3 %, худшая ±3.9 %); здесь — то, что держится формулой, и
тест на настоящем слайсере, когда он есть.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from datum.fab.mass import cached_print_mass, print_mass, uniform_props, upright_matrix
from datum.fab.settings import FALLBACK, SettingsError, resolve
from datum.geom import box, cyl_z, union

SOURCES = {k: "тест" for k in FALLBACK}


def _solid_settings(**part):
    """Плотность 1 г/см³, подача 1, сплошное — масса = объём, инерция = формула."""
    return resolve({**FALLBACK, "flow": 1.0, "density_g_cm3": 1.0}, SOURCES, layer=0.2,
                   part={"infill": 1.0, **part})


def test_a_solid_box_weighs_its_volume_and_has_the_textbook_inertia():
    s = _solid_settings()
    a, b, c = 40.0, 20.0, 10.0
    mp = print_mass(box(0, a, 0, b, 0, c), s, up="+Z")
    m = a * b * c / 1000.0                                   # г при 1 г/см³
    assert mp.mass_g == pytest.approx(m, rel=1e-9)
    assert mp.com_mm == pytest.approx((a / 2, b / 2, c / 2), abs=1e-9)
    i = np.array(mp.inertia_g_mm2)
    assert i[0, 0] == pytest.approx(m * (b * b + c * c) / 12, rel=1e-3)   # слои по Z: c²/12 точно
    assert i[1, 1] == pytest.approx(m * (a * a + c * c) / 12, rel=1e-3)
    assert i[2, 2] == pytest.approx(m * (a * a + b * b) / 12, rel=1e-9)
    assert abs(i[0, 1]) < 1e-6 * m * a * a


def test_print_pose_rotation_matches_upright_and_the_answer_returns_to_the_script_frame():
    """Деталь, лежащая на боку, режется в позе печати, но центр масс и
    инерция — в системе её скрипта: в механизме она стоит как построена."""
    from datum.geom.convert import vertices_faces
    from datum.geom.xform import upright

    part = union([box(0, 30, 0, 10, 0, 5), box(0, 5, 0, 10, 5, 25)])     # Г-образная
    for up in ("+Z", "-Z", "+X", "-X", "+Y", "-Y"):
        r = upright_matrix(up)
        v0 = np.asarray(vertices_faces(part.manifold)[0])
        v1 = np.asarray(vertices_faces(upright(part, up).manifold)[0])
        assert np.allclose(np.sort((v0 @ r.T).round(6), axis=0), np.sort(v1.round(6), axis=0))
        mp = print_mass(part, _solid_settings(), up=up)
        assert mp.com_mm == pytest.approx(print_mass(part, _solid_settings(), up="+Z").com_mm,
                                          abs=0.05), up


def test_infill_lowers_the_mass_and_walls_raise_it():
    part = box(0, 60, 0, 40, 0, 20)
    s = lambda **k: resolve(FALLBACK, SOURCES, layer=0.2, part=k)            # noqa: E731
    light = print_mass(part, s(walls=2, infill=0.1), up="+Z").mass_g
    heavy = print_mass(part, s(walls=2, infill=0.5), up="+Z").mass_g
    walls = print_mass(part, s(walls=6, infill=0.1), up="+Z").mass_g
    solid = 60 * 40 * 20 / 1000 * FALLBACK["density_g_cm3"] * FALLBACK["flow"]
    assert light < walls < solid and light < heavy < solid


def test_a_slope_keeps_its_shell_thickness_along_the_normal():
    """Конус: стенка по нормали к скату — как у слайсера с
    ensure_vertical_shell_thickness; без правила шестерни Gear Cube выходили
    на 6–9 % легче G-code."""
    from datum.geom import cone_z

    cone = cone_z(60, 4, 0, 30)
    s = resolve(FALLBACK, SOURCES, layer=0.2, part={"walls": 2, "infill": 0.0})
    flat_only = print_mass(cone, s, up="+Z")
    assert 0.05 < flat_only.shell_fraction < 1.0


def test_purchased_mass_has_the_shape_inertia():
    m, c, i = uniform_props(cyl_z(40, 0, 10), 100.0)
    assert m == 100.0 and c[2] == pytest.approx(5.0, abs=1e-6)
    assert i[2, 2] == pytest.approx(0.5 * 100 * 20 ** 2, rel=0.01)


def test_the_cache_returns_the_same_numbers_and_tracks_the_settings(tmp_path):
    part = box(0, 30, 0, 20, 0, 10)
    a = _solid_settings()
    first = cached_print_mass(part, a, up="+Z", cache_dir=tmp_path)
    again = cached_print_mass(part, a, up="+Z", cache_dir=tmp_path)
    assert again.mass_g == first.mass_g and again.inertia_g_mm2 == first.inertia_g_mm2
    other = cached_print_mass(part, resolve(FALLBACK, SOURCES, layer=0.2, part={"infill": 0.1}),
                              up="+Z", cache_dir=tmp_path)
    assert other.mass_g < first.mass_g
    assert len(list(tmp_path.glob("*.json"))) == 2


@pytest.mark.parametrize("cfg, words", [
    ({"walls": 0}, "целое 1…20"), ({"infill": 15}, "15%"), ({"infill": "много"}, "параметр"),
    ({"stenki": 3}, "непонятные"), ({"walls": float("nan")}, "число"),
])
def test_settings_that_cannot_be_understood_are_refused_in_words(cfg, words):
    with pytest.raises(SettingsError, match=words):
        resolve(FALLBACK, SOURCES, layer=0.2, part=cfg, read_param=lambda n: 1 / 0)


def test_a_setting_can_name_a_parameter_and_only_set_values_go_to_the_slicer():
    s = resolve(FALLBACK, {k: "профиль X" for k in FALLBACK}, layer=0.2,
                project={"walls": 3}, part={"infill": "INFILL"}, part_name="рычаг",
                read_param=lambda n: {"INFILL": 0.35}[n])
    assert s.walls == 3 and s.infill == 0.35
    assert "параметр INFILL" in s.sources["infill"] and s.sources["walls"] == "datum.toml [print]"
    assert s.slicer_overrides() == {"wall_loops": "3", "sparse_infill_density": "35%"}


def test_solid_layers_count_by_the_nominal_layer():
    """1.0 мм верха при слое 0.2 — 5 слоёв, даже если деталь режется по 0.198."""
    s = resolve({**FALLBACK, "top_thickness": 1.0}, SOURCES, layer=0.2, part={"top_layers": 3})
    assert s.top_count == 5


def test_part_grams_counts_only_the_part(tmp_path):
    from datum.io.slicer import part_grams

    g = tmp_path / "x.gcode"
    g.write_text("M83\n;TYPE:Custom\nG1 X1 E10\n;TYPE:Outer wall\nG1 X2 E5\nG1 E-1\nG1 E1\n"
                 ";TYPE:Brim\nG1 X3 E7\n;TYPE:Sparse infill\nG1 X4 E2.5\n", encoding="utf-8")
    area = math.pi * 0.875 ** 2
    assert part_grams(g, density=1.25) == pytest.approx(7.5 * area * 1.25 / 1000)


# --------------------------------------------------- настоящий слайсер
def _real() -> bool:
    from datum.io.slicer import SlicerError, slicer_command, system_dir

    try:
        slicer_command()
    except SlicerError:
        return False
    return (system_dir() / "Elegoo.json").is_file()


@pytest.mark.skipif(not _real(), reason="ElegooSlicer не установлен")
def test_the_mass_model_agrees_with_a_real_slice(tmp_path):
    """Деталь с полками, пазом и скатом — масса Datum против пластика детали в
    G-code настоящего ElegooSlicer, с теми же настройками."""
    from datum.fab.settings import from_profiles
    from datum.geom import cone_z, diff
    from datum.io.export import on_bed, write_3mf
    from datum.io.slicer import Profiles, choose, part_grams, slice_model

    part = union([box(0, 50, 0, 30, 0, 8), box(0, 12, 0, 30, 8, 30), cone_z(20, 6, 8, 22, 32, 15)])
    part = diff(part, [box(20, 40, 10, 20, 4, 9)])
    base, src = from_profiles({"profile": "elegoo-cc2-0.4", "nozzle": 0.4, "layer": 0.2,
                               "material": "PLA"})
    s = resolve(base, src, layer=0.2, part={"walls": 3, "infill": 0.2})
    est = print_mass(part, s, up="+Z").mass_g
    work = tmp_path / "work"
    work.mkdir()
    placed = on_bed(part, "+Z", (256, 256, 256))
    model = write_3mf(work / "p.3mf", placed, title="деталь")
    p = Profiles()
    c = choose(p, printer_profile="elegoo-cc2-0.4", nozzle=0.4, layer=0.2, material="PLA")
    cut = slice_model(model.path, tmp_path / "o.3mf", tmp_path / "o.gcode", profiles=p, choice=c,
                      triangles=model.triangles, overrides=s.slicer_overrides(), work=work)
    real = part_grams(cut.gcode, density=s.density_g_cm3)
    assert est == pytest.approx(real, rel=0.06), (est, real)
