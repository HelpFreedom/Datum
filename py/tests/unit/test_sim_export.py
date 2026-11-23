"""Механизм наружу: GLB с движением, MJCF, URDF — и сборочная инструкция.

Всё, что записано, перечитывается: GLB — разбором своих же буферов против
кадров проигрывателя, MJCF и URDF — MuJoCo против модели, по которой шёл
расчёт. Файл, не совпавший с проектом, хуже отсутствующего.
"""

from __future__ import annotations

import asyncio
import json
import math
import shutil
import struct
from pathlib import Path

import numpy as np
import pytest

from datum.fab.settings import FALLBACK, resolve
from datum.geom import box, cyl_z
from datum.io.glb import links_to_glb
from datum.sim import Mechanism, drive, revolute
from datum.sim.compile import compile_mechanism
from datum.sim.mechanism import MechanismError, prismatic
from datum.tools import ops, ops_guide, ops_sim_export

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def arm(tmp_path):
    root = tmp_path / "arm"
    shutil.copytree(ROOT / "examples" / "arm", root,
                    ignore=shutil.ignore_patterns(".datum", "output", "snapshots"))
    ops.clear_project_cache()
    yield root
    ops.clear_project_cache()


def _glb(data: bytes):
    """(json, bin) из GLB."""
    magic, version, total = struct.unpack_from("<III", data, 0)
    assert magic == 0x46546C67 and version == 2 and total == len(data)
    n, kind = struct.unpack_from("<II", data, 12)
    assert kind == 0x4E4F534A
    doc = json.loads(data[20:20 + n])
    m, kind = struct.unpack_from("<II", data, 20 + n)
    assert kind == 0x004E4942
    return doc, data[28 + n:28 + n + m]


def _accessor(doc, blob, index: int) -> np.ndarray:
    acc = doc["accessors"][index]
    view = doc["bufferViews"][acc["bufferView"]]
    width = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}[acc["type"]]
    dtype = {5126: np.float32, 5123: np.uint16, 5125: np.uint32}[acc["componentType"]]
    assert view["byteOffset"] % 4 == 0, "вид обязан быть выровнен на 4"
    out = np.frombuffer(blob, dtype=dtype, count=acc["count"] * width, offset=view["byteOffset"])
    return out.reshape(-1, width) if width > 1 else out


def _quat_matrix(q) -> np.ndarray:
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


# ------------------------------------------------------------------ GLB
def _two_links():
    cube = (np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 10]], float),
            np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]))
    return [("база", *cube, "#808080"), ("рычаг", *cube, "#ff0000")]


def test_a_static_glb_is_y_up_metres_with_a_node_per_link():
    doc, blob = _glb(links_to_glb(_two_links(), name="мех"))
    root = doc["nodes"][0]
    assert root["scale"] == [0.001] * 3 and root["children"] == [1, 2]
    # Z Datum → Y glTF: поворот корня переводит (0, 0, 1) в (0, 1, 0).
    assert _quat_matrix(root["rotation"]) @ [0, 0, 1] == pytest.approx([0, 1, 0], abs=1e-12)
    assert [n["name"] for n in doc["nodes"][1:]] == ["база", "рычаг"]
    assert "animations" not in doc
    pos = _accessor(doc, blob, doc["meshes"][1]["primitives"][0]["attributes"]["POSITION"])
    assert pos.max() == pytest.approx(10.0), "сетки остаются в миллиметрах Datum"
    assert "NORMAL" not in doc["meshes"][0]["primitives"][0]["attributes"]


def test_only_moving_links_get_tracks_and_quaternions_do_not_flip():
    t = [0.0, 0.5, 1.0, 1.5]
    still = [0, 0, 0, 1, 0, 0, 0]
    poses = []
    for k, angle in enumerate((0.0, 3.0, 3.2, 6.0)):       # через π: знак q меняется
        c, s = math.cos(angle / 2), math.sin(angle / 2)
        q = [c, 0, 0, s] if k != 2 else [-c, 0, 0, -s]      # тот же поворот с другим знаком
        poses.append([still, [5.0 * k, 0, 0, *q]])
    doc, blob = _glb(links_to_glb(_two_links(), times=t, poses=poses, animation="ход"))
    clip = doc["animations"][0]
    assert clip["name"] == "ход"
    assert {(c["target"]["node"], c["target"]["path"]) for c in clip["channels"]} == \
        {(2, "translation"), (2, "rotation")}, "стоящее звено дорожки не получает"
    by_path = {c["target"]["path"]: clip["samplers"][c["sampler"]] for c in clip["channels"]}
    clock = _accessor(doc, blob, by_path["rotation"]["input"])
    assert clock.tolist() == pytest.approx(t)
    assert doc["accessors"][by_path["rotation"]["input"]]["max"] == [1.5]
    quats = _accessor(doc, blob, by_path["rotation"]["output"])
    assert all(float(a @ b) > 0 for a, b in zip(quats, quats[1:])), "соседние кадры — одним знаком"
    moves = _accessor(doc, blob, by_path["translation"]["output"])
    assert moves[:, 0].tolist() == pytest.approx([0, 5, 10, 15])


def test_frames_of_the_wrong_shape_are_refused():
    with pytest.raises(ValueError, match="кадры формы"):
        links_to_glb(_two_links(), times=[0, 1], poses=[[[0] * 7]] * 2)
    with pytest.raises(ValueError, match="строго расти"):
        links_to_glb(_two_links(), times=[0, 0], poses=[[[0, 0, 0, 1, 0, 0, 0]] * 2] * 2)


# ------------------------------------------------------------- операция
def _export(root, *a, **kw):
    return asyncio.run(ops_sim_export.sim_export(str(root), *a, **kw))


def test_the_arm_goes_out_in_three_formats_and_each_is_read_back(arm):
    res = _export(arm, "рука", "перенос")
    assert "error" not in res, res
    files = res["files"]
    out = arm / "output" / "sim" / "рука"
    assert (arm / files["glb"]["file"]).is_file() and files["glb"]["frames"] > 10
    assert files["mjcf"]["bodies"] == 4, "три звена и груз сценария"
    assert files["mjcf"]["joints"] == 2 and files["mjcf"]["actuators"] == 2
    assert files["mjcf"]["worst_m"] < 1e-5 and files["urdf"]["worst_m"] < 1e-8
    assert sorted(p.name for p in (out / "meshes").iterdir()) == \
        ["основание.stl", "плечо.stl", "предплечье.stl"]
    assert "перечитано MuJoCo" in res["report"] and "грузы сценария в моделях: груз" in res["report"]

    # GLB против записи проигрывателя: то же положение звена в том же кадре.
    from datum.doc.project import Project

    pr = Project.open(arm)
    _, r = pr.simulate("рука", "перенос", geometry=False)
    doc, blob = _glb((arm / files["glb"]["file"]).read_bytes())
    names = [n["name"] for n in doc["nodes"]]
    clip = doc["animations"][0]
    k = len(r.frames["t"]) // 2
    for ch in clip["channels"]:
        link = names[ch["target"]["node"]]
        pose = r.frames["poses"][k][r.frames["links"].index(link)]
        got = _accessor(doc, blob, clip["samplers"][ch["sampler"]]["output"])[k]
        if ch["target"]["path"] == "translation":
            assert got == pytest.approx(pose[0:3], abs=1e-3)
        else:
            want = np.array([pose[4], pose[5], pose[6], pose[3]])
            assert abs(float(got @ want)) == pytest.approx(1.0, abs=1e-6)
    assert {names[c["target"]["node"]] for c in clip["channels"]} == {"плечо", "предплечье"}
    assert _accessor(doc, blob, clip["samplers"][0]["input"])[-1] == pytest.approx(
        r.frames["t"][-1], abs=1e-4)


def test_without_a_scenario_nothing_moves_and_no_payload_is_carried(arm):
    res = _export(arm, "рука")
    assert res["files"]["glb"]["frames"] == 0 and res["files"]["mjcf"]["bodies"] == 3
    doc, _ = _glb((arm / res["files"]["glb"]["file"]).read_bytes())
    assert "animations" not in doc
    assert "грузов в моделях нет" in res["report"]
    assert "груз" not in (arm / res["files"]["urdf"]["file"]).read_text("utf-8").split("<robot")[1]


def test_only_the_asked_formats_are_written_and_the_unknown_is_refused(arm):
    res = _export(arm, "рука", "", ["urdf"])
    assert set(res["files"]) == {"urdf", "meshes"}
    assert not (arm / "output" / "sim" / "рука" / "model.xml").exists()
    assert "есть glb, mjcf, urdf" in _export(arm, "рука", "", ["step"])["error"]
    assert "нет сценария" in _export(arm, "рука", "полёт")["error"]
    assert "нет механизма" in _export(arm, "нога")["error"]


# ------------------------------------------------- ведомый сустав и ползун
def _solid(_name):
    s = resolve({**FALLBACK, "flow": 1.0, "density_g_cm3": 1.0}, {k: "тест" for k in FALLBACK},
                layer=0.2, part={"infill": 1.0})
    return s, "+Z"


def _geared_with_slider():
    m = Mechanism(base="clamped")
    m.link("база").add(box(-20, 20, -20, 20, -30, -20), mass_g=500)
    m.link("вал", parent="база", joint=revolute("+Z", [0, 0, 0], [-720, 720], drive=drive(1.0, 60))
           ).add(cyl_z(10, -20, 0), mass_g=20)
    m.link("рычаг", parent="база",
           joint=revolute("-Y", [0, 0, 50], [-360, 360], follows=("вал", 0.5))
           ).add(box(195, 205, -5, 5, 45, 55), mass_g=300)
    m.link("ползун", parent="рычаг", joint=prismatic("+X", [0, 40], drive=drive(20, 30))
           ).add(box(100, 110, -5, 5, 60, 70), mass_g=50)
    return m


def test_a_follower_and_a_slider_survive_both_formats(tmp_path):
    from datum.geom.booleans import compose
    from datum.io.export import write_stl
    from datum.sim.model import check_urdf, write_mjcf
    from datum.sim.urdf import urdf_text

    comp = compile_mechanism(_geared_with_slider(), part_settings=_solid, cache_dir=tmp_path)
    meshes = {}
    for n, lk in comp.links.items():
        write_stl(tmp_path / "out" / "meshes" / f"{n}.stl", compose(lk.solids))
        meshes[n] = f"{n}.stl"
    info = write_mjcf(comp, tmp_path / "out" / "model.xml", name="мех", meshes=meshes)
    assert info["couplings"] == 1 and info["joints"] == 3 and info["worst_m"] < 1e-5
    xml = (tmp_path / "out" / "model.xml").read_text("utf-8")
    assert 'joint1="рычаг" joint2="вал" polycoef="0 0.5 0 0 0"' in xml
    assert 'type="slide"' in xml and 'range="0 0.04"' in xml, "ход ползуна — в метрах"

    text = urdf_text(comp, name="мех", meshes={n: f"meshes/{f}" for n, f in meshes.items()})
    path = tmp_path / "out" / "robot.urdf"
    path.write_text(text, encoding="utf-8")
    assert check_urdf(comp, path) < 1e-8
    assert '<mimic joint="вал" multiplier="0.5" offset="0"/>' in text
    assert 'type="prismatic"' in text and 'upper="0.04"' in text
    assert 'velocity="0.03"' in text, "скорость ползуна 30 мм/с — в м/с"


def test_a_wrong_file_is_caught_by_the_read_back(tmp_path):
    """Сверка обязана ловить то, ради чего она есть: URDF с рамкой не там."""
    from datum.sim.model import check_urdf
    from datum.sim.urdf import urdf_text

    comp = compile_mechanism(_geared_with_slider(), part_settings=_solid, cache_dir=tmp_path)
    text = urdf_text(comp, name="мех", meshes={})
    assert '<origin xyz="0 0 0.05" rpy="0 0 0"/>' in text
    path = tmp_path / "robot.urdf"
    path.write_text(text.replace('<origin xyz="0 0 0.05" rpy="0 0 0"/>',
                                 '<origin xyz="0 0 0.06" rpy="0 0 0"/>', 1), encoding="utf-8")
    with pytest.raises(MechanismError, match="расходится"):
        check_urdf(comp, path)


# ------------------------------------------------------ сборочная инструкция
def _guide(root, **kw):
    return asyncio.run(ops_guide.assembly_guide(str(root), **kw))


def test_the_guide_has_a_picture_and_a_sentence_per_step(latch):
    ops.clear_project_cache()
    out = latch / "output" / "guide"
    out.mkdir(parents=True)
    (out / "step-07.png").write_bytes(b"old")           # шаг от прежнего, более длинного порядка
    res = _guide(latch, title="Коробка <с> защёлкой")
    assert [s["part"] for s in res["steps"]] == ["корпус", "крышка"]
    assert "Основание" in res["steps"][0]["text"]
    lid = res["steps"][1]
    assert lid["method"] == "snap" and lid["insert"] == ["-Z"]
    assert "сверху вниз до щелчка" in lid["text"]
    from PIL import Image

    first = Image.open(latch / res["steps"][0]["image"]["path"])
    second = Image.open(latch / lid["image"]["path"])
    assert second.width == 2 * first.width, "у шага со вставкой — «до» и «после»"
    assert not (out / "step-07.png").exists(), "лишние шаги прежней инструкции убраны"
    page = (latch / res["page"]).read_text("utf-8")
    assert "Коробка &lt;с&gt; защёлкой" in page and 'src="step-02.png"' in page
    assert "http" not in page, "страница открывается без сети"
    ops.clear_project_cache()


def test_no_declared_order_is_a_refusal_in_words(cube):
    ops.clear_project_cache()
    assert "порядок сборки не записан" in _guide(cube)["error"]
    ops.clear_project_cache()
