"""Нарезка в ElegooSlicer (`io/slicer.py`): профили, выбор, сверка нарезанного.

Модульные проверки идут на синтетическом каталоге профилей и поддельном
слайсере — им не нужен настоящий. Последняя проверка — с настоящим
ElegooSlicer, если он стоит (как py/tests/corpus — внешний корпус проектов).
"""

from __future__ import annotations

import json
import shutil
import sys
import textwrap
from pathlib import Path

import pytest

from datum.io.slicer import (Profiles, SlicerError, choose, parse_gcode, slice_model,
                             system_dir)

PRINTER = "Elegoo Centauri Carbon 2 0.4 nozzle"


def _profiles(tmp_path: Path) -> Path:
    """Мини-каталог: машина, два процесса, три пластика, двойник в чужом каталоге."""
    root = tmp_path / "system"
    files = {
        "machine/common.json": {"name": "fdm_machine_common", "instantiation": "false",
                                "nozzle_diameter": ["0.4"], "default_bed_type": "4"},
        "machine/cc2.json": {"name": PRINTER, "inherits": "fdm_machine_common",
                             "instantiation": "true", "printer_model": "Elegoo Centauri Carbon 2"},
        "process/common.json": {"name": "fdm_process_common", "instantiation": "false",
                                "layer_height": "0.2", "wall_loops": "2", "line_width": "0.42"},
        "process/std.json": {"name": "0.20mm Standard @Elegoo CC2 0.4 nozzle",
                             "inherits": "fdm_process_common", "instantiation": "true",
                             "compatible_printers": [PRINTER]},
        "process/fine.json": {"name": "0.12mm Fine @Elegoo CC2 0.4 nozzle",
                              "inherits": "0.20mm Standard @Elegoo CC2 0.4 nozzle",
                              "instantiation": "true", "layer_height": "0.12"},
        "filament/pla_base.json": {"name": "Elegoo PLA @base", "instantiation": "false",
                                   "filament_type": ["PLA"], "filament_id": "EPLAB00",
                                   "filament_density": ["1.25"]},
        "filament/pla.json": {"name": "Elegoo PLA @ECC2", "inherits": "Elegoo PLA @base",
                              "instantiation": "true", "compatible_printers": [PRINTER]},
        "filament/pla_silk.json": {"name": "Elegoo PLA Silk @ECC2", "inherits": "Elegoo PLA @base",
                                   "instantiation": "true", "compatible_printers": [PRINTER]},
    }
    lists = {"machine_list": [], "process_list": [], "filament_list": []}
    for sub, data in files.items():
        path = root / "Elegoo" / sub
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        lists[sub.split("/")[0] + "_list"].append({"name": data["name"], "sub_path": sub})
    (root / "Elegoo.json").write_text(json.dumps({"name": "Elegoo", **lists}), encoding="utf-8")
    # Двойник базового профиля в чужой библиотеке — так агент взял чужой filament_id.
    other = root / "OrcaFilamentLibrary" / "filament" / "pla_base.json"
    other.parent.mkdir(parents=True)
    other.write_text(json.dumps({"name": "Elegoo PLA @base", "filament_id": "OGFL99",
                                 "filament_type": ["PLA"]}), encoding="utf-8")
    return root


# ----------------------------------------------------------------- профили
def test_the_chain_is_resolved_inside_the_vendor(tmp_path):
    p = Profiles(_profiles(tmp_path))
    pla = p.resolve("filament", "Elegoo PLA @ECC2")
    assert pla["filament_id"] == "EPLAB00", "родитель — из манифеста Elegoo, не двойник"
    assert pla["filament_density"] == ["1.25"] and "inherits" not in pla
    fine = p.resolve("process", "0.12mm Fine @Elegoo CC2 0.4 nozzle")
    assert fine["layer_height"] == "0.12" and fine["line_width"] == "0.42"


def test_compatibility_is_inherited(tmp_path):
    p = Profiles(_profiles(tmp_path))
    assert p.presets("process", printer=PRINTER) == [
        "0.12mm Fine @Elegoo CC2 0.4 nozzle", "0.20mm Standard @Elegoo CC2 0.4 nozzle"]


def test_choice_follows_the_manifest(tmp_path):
    p = Profiles(_profiles(tmp_path))
    c = choose(p, printer_profile="elegoo-cc2-0.4", nozzle=0.4, layer=0.12, material="PLA")
    assert (c.printer, c.process, c.filament) == (
        PRINTER, "0.12mm Fine @Elegoo CC2 0.4 nozzle", "Elegoo PLA @ECC2")
    assert c.bed_type == "Textured PEI Plate", "умолчание стола машины — его CLI не ставит"


@pytest.mark.parametrize("kwargs, match", [
    ({"layer": 0.3}, "нет процесса со слоем 0.3"),
    ({"material": "PETG"}, "пластик для 'PETG'"),
    ({"printer_profile": "prusa-mk4-0.4"}, "не знаком нарезке"),
    ({"nozzle": 0.6}, "какое верно, знает человек"),
    ({"filament": "Elegoo PLA @base"}, "не подходит"),
])
def test_what_cannot_be_chosen_is_refused(tmp_path, kwargs, match):
    p = Profiles(_profiles(tmp_path))
    args = {"printer_profile": "elegoo-cc2-0.4", "nozzle": 0.4, "layer": 0.2, "material": "PLA",
            **kwargs}
    with pytest.raises(SlicerError, match=match):
        choose(p, **args)


def test_a_missing_vendor_is_explained(tmp_path):
    with pytest.raises(SlicerError, match="откройте окно ElegooSlicer"):
        Profiles(tmp_path / "нет")


# --------------------------------------------------------------- нарезка
FAKE = textwrap.dedent('''\
    """Поддельный слайсер: пишет то, что пишет настоящий, из загруженных профилей."""
    import json, sys, zipfile
    from pathlib import Path

    args = sys.argv[1:]
    get = lambda k: args[args.index(k) + 1]
    cfg = {}
    for f in get("--load-settings").split(";") + [get("--load-filaments")]:
        cfg.update(json.loads(Path(f).read_text(encoding="utf-8")))
    out = Path(get("--outputdir"))
    drop = Path(__file__).with_name("drop.txt")
    if drop.is_file():
        cfg.pop(drop.read_text().strip(), None)          # «забыть» правку
    ids = {"printer_settings_id": "machine", "print_settings_id": "process",
           "filament_settings_id": "filament"}
    footer = ["; total filament used [g] = 12.34", "; total layers count = 50",
              "; estimated printing time (normal mode) = 1h 2m 3s"]
    if Path(__file__).with_name("nosummary.txt").is_file():
        footer = footer[2:]                               # ни граммов, ни слоёв
    for k, v in cfg.items():
        footer.append(f"; {k} = {','.join(v) if isinstance(v, list) else v}")
    names = {}
    for f, kind in zip(get("--load-settings").split(";"), ("machine", "process")):
        names[kind] = json.loads(Path(f).read_text(encoding="utf-8"))["name"]
    names["filament"] = json.loads(Path(get("--load-filaments")).read_text(encoding="utf-8"))["name"]
    footer += [f"; {k} = {names[v]}" for k, v in ids.items()]
    (out / "plate_1.gcode").write_text("G28\\n" + "\\n".join(footer) + "\\n", encoding="utf-8")
    zipfile.ZipFile(out / get("--export-3mf"), "w").writestr("Metadata/x", "1")
    tris = int(Path(__file__).with_name("tris.txt").read_text())
    (out / "result.json").write_text(json.dumps({
        "return_code": 0, "error_string": "Success.",
        "sliced_plates": [{"id": 1, "triangle_count": tris, "warning_message": ""}]}))
''')


def _fake(tmp_path: Path, tris: int = 12) -> list[str]:
    script = tmp_path / "fake_slicer.py"
    script.write_text(FAKE, encoding="utf-8")
    (tmp_path / "tris.txt").write_text(str(tris))
    return [sys.executable, str(script)]


def _slice(tmp_path, *, overrides=None, triangles=12):
    p = Profiles(_profiles(tmp_path))
    c = choose(p, printer_profile="elegoo-cc2-0.4", nozzle=0.4, layer=0.2, material="PLA")
    model = tmp_path / "деталь.3mf"
    model.write_bytes(b"3mf")
    out = tmp_path / "output"
    return slice_model(model, out / "деталь_PLA.3mf", out / "деталь_PLA.gcode", profiles=p,
                       choice=c, triangles=triangles, overrides=overrides,
                       work=tmp_path / "work", command=_fake(tmp_path))


def test_a_slice_is_kept_when_it_is_what_was_asked(tmp_path):
    cut = _slice(tmp_path, overrides={"wall_loops": "4"})
    assert cut.project.is_file() and cut.gcode.is_file()
    assert (cut.time, cut.grams, cut.layers) == ("1h 2m 3s", 12.34, 50)
    assert cut.config["wall_loops"] == "4"
    assert cut.config["curr_bed_type"] == "Textured PEI Plate"
    assert not list((tmp_path / "work").iterdir()), "рабочий каталог убран"


def test_a_dropped_override_is_caught_and_nothing_is_left(tmp_path):
    (tmp_path / "drop.txt").write_text("wall_loops")
    with pytest.raises(SlicerError, match="нарезано не тем, что задано: wall_loops"):
        _slice(tmp_path, overrides={"wall_loops": "4"})
    assert not (tmp_path / "output").exists()


def test_gcode_without_a_summary_is_refused_before_the_files_land(tmp_path):
    """Прежде нехватка строки давала массу nan уже ПОСЛЕ перекладки файлов."""
    (tmp_path / "nosummary.txt").write_text("1")
    with pytest.raises(SlicerError, match="нет сводки"):
        _slice(tmp_path)
    assert not (tmp_path / "output").exists()


def test_a_different_triangle_count_is_refused(tmp_path):
    with pytest.raises(SlicerError, match="нарезал 12 треугольников, а у детали 99"):
        _slice(tmp_path, triangles=99)


def test_an_unknown_setting_is_refused_before_slicing(tmp_path):
    with pytest.raises(SlicerError, match="нет таких настроек"):
        _slice(tmp_path, overrides={"walls_count": "4"})


def test_the_gcode_tail_is_parsed(tmp_path):
    g = tmp_path / "x.gcode"
    g.write_text("G1 X0\n" * 1000 + "; layer_height = 0.2\n; filament_settings_id = \"Elegoo PLA @ECC2\"\n")
    cfg = parse_gcode(g)
    assert cfg["layer_height"] == "0.2" and cfg["filament_settings_id"] == '"Elegoo PLA @ECC2"'


# ------------------------------------------------------- настоящий слайсер
def _real() -> bool:
    from datum.io.slicer import slicer_command

    try:
        slicer_command()
    except SlicerError:
        return False
    return (system_dir() / "Elegoo.json").is_file()


@pytest.mark.skipif(not _real(), reason="ElegooSlicer не установлен")
def test_a_real_slice_of_a_box(tmp_path):
    """Настоящий слайсер режет брусок теми профилями, что выбраны по datum.toml.

    Замер, ради которого разрешение цепочки живёт в Datum: без него консольный
    слайсер резал с шириной линий 0 и плотностью пластика 0.
    """
    from datum.geom import box
    from datum.io.export import on_bed, write_3mf

    work = tmp_path / "work"
    work.mkdir()
    model = write_3mf(work / "brick.3mf", on_bed(box(0, 20, 0, 10, 0, 5), "+Z", (256, 256, 256)),
                      title="брусок")
    p = Profiles()
    c = choose(p, printer_profile="elegoo-cc2-0.4", nozzle=0.4, layer=0.2, material="PLA")
    cut = slice_model(model.path, tmp_path / "out" / "b.3mf", tmp_path / "out" / "b.gcode",
                      profiles=p, choice=c, triangles=model.triangles,
                      overrides={"wall_loops": "3"}, work=work)
    cfg = cut.config
    assert float(cfg["layer_height"]) == pytest.approx(0.2)
    assert float(cfg["line_width"]) > 0.3, "ширина линий из цепочки, а не встроенный ноль"
    assert float(cfg["filament_density"]) > 1.0
    assert cfg["wall_loops"] == "3" and cut.grams > 0 and cut.layers == 25
    shutil.rmtree(tmp_path / "out")


def test_booleans_and_percent_are_compared_honestly():
    from datum.io.slicer import _same

    assert _same("50%", "50%") and not _same("50%", "50") and not _same("50", "50%")
    assert _same("0.2", "0.20") and _same('"Elegoo PLA @ECC2"', ["Elegoo PLA @ECC2"])


def test_a_boolean_override_reaches_the_slicer_as_1(tmp_path):
    """str(True) — «True», а профиль слайсера ждёт «1»: сверка отказала бы."""
    cut = _slice(tmp_path, overrides={"wall_loops": True})
    assert cut.config["wall_loops"] == "1"


def test_a_path_with_a_semicolon_is_refused(tmp_path):
    p = Profiles(_profiles(tmp_path))
    c = choose(p, printer_profile="elegoo-cc2-0.4", nozzle=0.4, layer=0.2, material="PLA")
    bad = tmp_path / "a;b"
    bad.mkdir()
    with pytest.raises(SlicerError, match="«;»"):
        slice_model(bad / "m.3mf", bad / "o.3mf", bad / "o.gcode", profiles=p, choice=c,
                    triangles=12, work=bad / "work", command=_fake(tmp_path))


def test_a_hanging_slicer_is_stopped_with_its_children(tmp_path):
    """Таймаут снимает всю группу: обёртка distrobox оставила бы слайсер жить."""
    import os
    import time

    from datum.io.slicer import _run

    pidfile = tmp_path / "child.pid"
    script = tmp_path / "hang.sh"
    script.write_text(f"#!/bin/sh\nsleep 300 &\necho $! > {pidfile}\nwait\n")
    script.chmod(0o755)
    t0 = time.monotonic()
    with pytest.raises(SlicerError, match="не уложился"):
        _run([str(script)], tmp_path, timeout=1.0)
    assert time.monotonic() - t0 < 15
    child = int(pidfile.read_text())
    time.sleep(0.2)
    try:
        os.kill(child, 0)
        alive = os.path.exists(f"/proc/{child}") and "Z" not in open(f"/proc/{child}/stat").read().split()[2]
    except ProcessLookupError:
        alive = False
    assert not alive, "внук слайсера остался жить"
