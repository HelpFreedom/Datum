"""Выгрузка для слайсера: поза, файл, сверка записанного."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from datum.cli import main as cli
from datum.geom import box, diff
from datum.geom.measure import bbox, vol
from datum.io.export import ExportError, on_bed, safe_name, write_3mf, write_stl

ROOT = Path(__file__).resolve().parents[3]
BED = (256.0, 256.0, 256.0)


def _brick():
    """Брусок 10×20×5 с пазом сверху: позу по нему видно однозначно."""
    return diff(box(0, 10, 0, 20, 0, 5), [box(2, 8, 2, 18, 3, 6)]).renamed("брусок")


@pytest.mark.parametrize("up, height", [("+Z", 5), ("-Z", 5), ("+X", 10), ("-X", 10),
                                        ("+Y", 20), ("-Y", 20), ("", 5)])
def test_on_bed_stands_the_part_up_and_centres_it(up, height):
    placed = on_bed(_brick(), up, BED)
    lo, hi = bbox(placed)
    assert lo[2] == pytest.approx(0.0, abs=1e-6)
    assert hi[2] - lo[2] == pytest.approx(height, abs=1e-6)
    assert (lo[0] + hi[0]) / 2 == pytest.approx(128.0) and (lo[1] + hi[1]) / 2 == pytest.approx(128.0)
    assert vol(placed) == pytest.approx(vol(_brick()))


def test_the_pose_is_the_one_the_checks_saw():
    """Проверки печатности и выгрузка ставят деталь ОДНОЙ функцией."""
    from datum.checks.dfam import _oriented
    from datum.geom.xform import upright

    assert _oriented is upright


def test_stl_and_3mf_round_trip(tmp_path):
    import lib3mf
    import trimesh

    placed = on_bed(_brick(), "+X", BED)
    a = write_stl(tmp_path / "брусок.stl", placed)
    b = write_3mf(tmp_path / "брусок.3mf", placed, title="брусок", description="проверено")
    for exported in (a, b):
        mesh = trimesh.load(exported.path, force="mesh")
        assert mesh.is_watertight
        assert mesh.volume == pytest.approx(vol(_brick()), rel=1e-6)
        # Файл для слайсера читается всеми — не 0600, как у временного файла.
        assert exported.path.stat().st_mode & 0o044
    model = lib3mf.get_wrapper().CreateModel()
    model.QueryReader("3mf").ReadFromFile(str(b.path))
    assert model.GetUnit() == lib3mf.ModelUnit.MilliMeter
    meta = model.GetMetaDataGroup()
    got = {meta.GetMetaData(i).GetName(): meta.GetMetaData(i).GetValue()
           for i in range(meta.GetMetaDataCount())}
    assert got["Title"] == "брусок" and got["Description"] == "проверено"
    assert not list(tmp_path.glob(".*")), "временные файлы остались"



def test_writing_a_3mf_leaves_the_locale_alone(tmp_path):
    """lib3mf переводит процесс в локаль «C» и не возвращает — после этого
    кириллица без явной кодировки не пишется нигде в процессе."""
    import locale

    before = locale.setlocale(locale.LC_ALL)
    write_3mf(tmp_path / "брусок.3mf", on_bed(_brick(), "+Z", BED), title="брусок")
    assert locale.setlocale(locale.LC_ALL) == before
    (tmp_path / "заметка.txt").write_text("ок")          # без encoding — как в скриптах
    assert (tmp_path / "заметка.txt").read_bytes() == "ок".encode("utf-8")

def test_a_file_that_does_not_match_is_removed(tmp_path, monkeypatch):
    """Записанное сверяется с деталью; не сошлось — файла нет, есть отказ."""
    import trimesh

    real = trimesh.Trimesh.export

    def broken(self, *a, **k):
        # Писатель «потерял» треугольник: меш перестал быть замкнутым.
        return real(trimesh.Trimesh(self.vertices, self.faces[1:], process=False), *a, **k)

    monkeypatch.setattr(trimesh.Trimesh, "export", broken)
    with pytest.raises(ExportError, match="не совпало"):
        write_stl(tmp_path / "брусок.stl", on_bed(_brick(), "+Z", BED))
    assert not (tmp_path / "брусок.stl").exists()


def _edge_touching():
    """Два бруска, касающиеся ребром: в ядре одна деталь, в файле — общие вершины."""
    from datum.geom import union

    return union([box(0, 10, 0, 10, 0, 10), box(10, 20, 10, 20, 0, 10)])


@pytest.mark.parametrize("fmt", ["stl", "3mf"])
def test_vertices_that_would_merge_in_float32_are_separated(tmp_path, fmt, monkeypatch):
    """«Меш не замкнут» у замкнутой детали: STL и 3MF хранят float32, и вершины,
    разные в ядре, сливались при чтении. Так `export` отказывал на подставке
    под мышь, и агент разводил вершины в скрипте детали сам."""
    import trimesh

    import datum.io.precision as precision

    placed = on_bed(_edge_touching(), "+Z", BED)

    def write(path):
        return (write_stl(path, placed) if fmt == "stl"
                else write_3mf(path, placed, title="бруски"))

    # Без разведения — ровно отказ из отчёта агента.
    with monkeypatch.context() as m:
        m.setattr(precision, "separate_coincident",
                  lambda v, f, **k: precision.Separation(v, 0, 0.0, 0))
        with pytest.raises(ExportError, match="меш не замкнут"):
            write(tmp_path / f"было.{fmt}")

    out = write(tmp_path / f"стало.{fmt}")
    mesh = trimesh.load(out.path, force="mesh")
    assert mesh.is_watertight
    assert "разведено вершин" in out.note and "мкм" in out.note
    # Сдвиг — микрон: объём отличается на тысячные доли процента.
    assert out.volume == pytest.approx(vol(_edge_touching()), rel=1e-4)


def test_a_part_without_touching_sheets_is_written_untouched(tmp_path):
    from datum.geom.convert import vertices_faces
    from datum.io.precision import separate_coincident

    placed = on_bed(_brick(), "+X", BED)
    out = write_stl(tmp_path / "брусок.stl", placed)
    assert out.note == ""
    verts, faces = vertices_faces(placed.manifold)
    sep = separate_coincident(verts, faces)
    assert sep.moved == 0 and (sep.verts == verts).all()


def test_separation_moves_each_vertex_at_most_the_gap():
    from datum.geom.convert import vertices_faces
    from datum.io.precision import GAP_MM, coincident_pairs, separate_coincident

    verts, faces = vertices_faces(on_bed(_edge_touching(), "+Z", BED).manifold)
    assert len(coincident_pairs(verts)) == 2
    sep = separate_coincident(verts, faces)
    assert sep.moved == 2 and sep.left == 0
    assert sep.max_shift <= GAP_MM * (1 + 1e-9)
    assert len(coincident_pairs(sep.verts)) == 0


def test_safe_name_keeps_cyrillic_and_drops_separators():
    assert safe_name("крышка") == "крышка"
    assert safe_name("a/b\\c:d") == "a_b_c_d"
    with pytest.raises(ExportError):
        safe_name(" .. ")


def _copy(tmp_path, name):
    target = tmp_path / name
    shutil.copytree(ROOT / "examples" / name, target,
                    ignore=shutil.ignore_patterns(".datum", "output", "snapshots"))
    return target


def test_export_writes_and_warns_when_not_ready(tmp_path, capsys):
    """Выгружается всегда — но незамеренное называется первой строкой."""
    root = _copy(tmp_path, "latch")
    assert cli(["export", str(root), "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["ready"] is False and any("LIP_D" in w for w in d["warnings"])
    assert d["report"].startswith("ВНИМАНИЕ: проект не готов к печати")
    for name in ("корпус", "крышка"):
        assert (root / "output" / f"{name}.3mf").is_file()
        assert (root / "output" / f"{name}.stl").is_file()
    # Губка корпуса — кромка у стены, а не мост.
    assert d["parts"]["корпус"]["bridge_mm"] == 0.0


def test_export_poses_the_part_as_declared(tmp_path, capsys):
    """Флексура дозатора печатается на боку (print_up="+X"): 0 поддержек и
    1415 мм² первого слоя — те же числа, что у проверок печатности."""
    import trimesh

    root = _copy(tmp_path, "dispenser")
    assert cli(["export", str(root), "--parts", "флексура", "--format", "stl", "--no-checks",
                "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    part = d["parts"]["флексура"]
    assert part["print_up"] == "+X" and part["support_mm3"] == 0.0
    assert part["first_layer_mm2"] == pytest.approx(1414.7, abs=1.0)
    assert not (root / "output" / "флексура.3mf").exists()
    mesh = trimesh.load(root / "output" / "флексура.stl", force="mesh")
    assert mesh.bounds[0][2] == pytest.approx(0.0, abs=1e-4)
    # Проверки не прогонялись — значит и «готов» не сказано.
    assert d["ready"] is False and "не прогонялись" in d["checks"]


def test_unused_duplicate_vertices_are_not_reported():
    """Неиспользуемый двойник вершины (после ремапа слияния) — не слипание."""
    import numpy as np

    from datum.geom.convert import vertices_faces
    from datum.io.precision import separate_coincident

    verts, faces = vertices_faces(on_bed(_brick(), "+Z", BED).manifold)
    extra = np.vstack([verts, verts[:1]])              # двойник, никем не используемый
    sep = separate_coincident(extra, faces)
    assert sep.moved == 0 and sep.left == 0


def test_readiness_names_warnings_without_blocking(tmp_path):
    """Предупреждение печати не мешает — но вердикт его называет: «все
    проверки прошли» при опрокидывающейся статуэтке было бы неправдой."""
    from datum.doc.project import Project
    from datum.tools.ops import _readiness

    root = tmp_path / "мини"
    for sub in ("parts", "params", "checks"):
        (root / sub).mkdir(parents=True)
    (root / "params" / "base.yaml").write_text(
        "W: {value: 10, unit: mm, provenance: chosen, reason: '—'}\n", encoding="utf-8")
    (root / "parts" / "p.py").write_text(
        "from datum.build import P, part\nfrom datum.geom import box\n\n"
        "@part('брусок')\ndef build():\n    return box(0, P.W, 0, P.W, 0, P.W)\n",
        encoding="utf-8")
    (root / "checks" / "checks.yaml").write_text(
        '- id: стол.мягко\n  use: bed_contact\n  args: {part: "@брусок", min_area: 1000}\n'
        '  severity: warn\n', encoding="utf-8")
    warnings, verdict, description = _readiness(Project.open(root), True)
    assert warnings == []
    assert verdict.startswith("отказов нет из ")             # не «все прошли»
    assert verdict.endswith("; предупреждений 1: стол.мягко")
    assert description.startswith("ГОТОВ К ПЕЧАТИ")
