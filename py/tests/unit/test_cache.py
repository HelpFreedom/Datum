"""Контент-адресуемый кеш сборок."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import trimesh
from manifold3d import Manifold as M

from datum.build.cache import BuildCache, solid_from_bytes, solid_to_bytes
from datum.doc.project import Project, ProjectError
from datum.geom.convert import from_trimesh
from datum.geom.solid import Solid

ROOT = Path(__file__).resolve().parents[3]
LATCH = ROOT / "examples" / "latch"


@pytest.fixture
def work(latch):
    """Чистая копия примера — фикстура `latch` из conftest."""
    return latch


@pytest.fixture
def cache(tmp_path):
    with BuildCache(tmp_path / "cache", budget_mb=1) as c:
        yield c


def solid(man: M, name: str = "t") -> Solid:
    return Solid.wrap(man, name=name, op="t")


# ------------------------------------------------------------------ отпечаток
def test_digest_survives_serialisation():
    """Наивный отпечаток этого НЕ выдерживал: Manifold(Mesh(...)) перестраивает
    список граней, и деталь из кеша выглядела изменившейся."""
    s = solid(M.cube((10, 10, 10)) - M.cube((4, 4, 4)).translate((3, 3, 3)))
    back = solid_from_bytes(solid_to_bytes(s))
    assert s.digest() == back.digest()


def test_digest_ignores_face_order():
    s = solid(M.cube((10, 10, 10)) - M.cube((4, 4, 4)).translate((3, 3, 3)))
    mesh = s.to_trimesh()
    rng = np.random.default_rng(0)
    shuffled = trimesh.Trimesh(mesh.vertices.copy(),
                               mesh.faces[rng.permutation(len(mesh.faces))].copy(),
                               process=False)
    assert solid(from_trimesh(shuffled)).digest() == s.digest()


def test_digest_notices_real_change():
    a = solid(M.cube((10, 10, 10)) - M.cube((4, 4, 4)).translate((3, 3, 3)))
    b = solid(M.cube((10, 10, 10)) - M.cube((4.01, 4, 4)).translate((3, 3, 3)))
    assert a.digest() != b.digest()


def test_digest_of_empty_solid():
    assert solid(M()).digest()


# ------------------------------------------------------------------ сериализация
def test_roundtrip_preserves_geometry():
    src = solid(M.cylinder(15.0, 20.0, circular_segments=64))
    back = solid_from_bytes(solid_to_bytes(src))
    assert back.manifold.volume() == pytest.approx(src.manifold.volume(), rel=1e-5)
    assert back.manifold.num_tri() == src.manifold.num_tri()


def test_bad_payload_is_rejected():
    with pytest.raises(ValueError, match="заголовка"):
        solid_from_bytes("мусор".encode("utf-8"))


# ------------------------------------------------------------------ хранилище
def test_blob_put_and_get(cache):
    d = cache.put_blob("данные".encode("utf-8"), "bin", kind="проба")
    assert cache.get_blob(d, "bin") == "данные".encode("utf-8")
    assert cache.blob_path(d, "bin").is_file()
    assert cache.get_blob("нет" * 20, "bin") is None


def test_same_content_gives_same_digest(cache):
    assert cache.put_blob(b"x", "bin") == cache.put_blob(b"x", "bin")
    assert cache.stats()["objects"] == 1


def test_gc_drops_least_recently_used(cache):
    big = b"0" * 300_000
    for i in range(6):
        cache.put_blob(big + bytes([i]), "bin")
    assert cache.stats()["mb"] > 1.5
    res = cache.gc(budget_mb=1)
    assert res["removed"] > 0
    assert cache.stats()["mb"] <= 1.05


def test_clear(cache):
    cache.put_blob(b"x", "bin")
    cache.clear()
    assert cache.stats() == {**cache.stats(), "objects": 0, "builds": 0}


# ------------------------------------------------------------------ сборка
def test_second_build_comes_from_cache(work):
    cold = Project.open(work).build()
    warm = Project.open(work).build()
    assert not any(r.cached for r in cold.values())
    assert all(r.cached for r in warm.values())
    assert "из кеша" in warm["корпус"].line()


def test_cache_does_not_change_the_numbers(work):
    """Главное требование: кеш обязан быть невидим в результатах."""
    cold = Project.open(work).build()
    warm = Project.open(work).build()
    for name in cold:
        assert warm[name].topology.volume == cold[name].topology.volume, name
        assert warm[name].solid.digest() == cold[name].solid.digest(), name
        assert warm[name].topology.bodies == cold[name].topology.bodies


def test_parameter_change_misses_only_affected_parts(work):
    Project.open(work).build()
    pr = Project.open(work)
    res = pr.build(overrides={"LIP_D": 0.5})
    assert not res["корпус"].cached, "корпус читает LIP_D — обязан пересобраться"
    assert res["крышка"].cached, "крышка его не читает — должна остаться из кеша"


def test_source_change_invalidates(work):
    Project.open(work).build()
    f = work / "parts" / "lid.py"
    f.write_text(f.read_text(encoding="utf-8").replace(
        "P.LID_Z - P.LID_T, P.LID_Z", "P.LID_Z - P.LID_T - 0.5, P.LID_Z"),
        encoding="utf-8")
    res = Project.open(work).build()
    assert not res["крышка"].cached, "изменился код детали — кеш негоден"
    assert res["корпус"].cached


def test_disabled_cache_never_hits(work):
    Project.open(work).build()
    res = Project.open(work).build(use_cache=False)
    assert not any(r.cached for r in res.values())


def test_missing_blob_falls_back_to_rebuild(work):
    Project.open(work).build()
    pr = Project.open(work)
    for p in (work / ".datum" / "cache" / "objects").rglob("*.mesh"):
        p.unlink()
    res = pr.build()
    assert all(r.ok for r in res.values())
    assert not any(r.cached for r in res.values())


# ------------------------------------------------------------------ ветвление
BRANCH = '''
from datum.build import P, part
from datum.geom import box


@part("ветвление")
def build():
    if P.MODE > 0:
        return box(0, P.A, 0, 5, 0, 5)
    return box(0, P.B, 0, 5, 0, 5)
'''

PARAMS = '''
MODE: {value: 1.0, unit: "", provenance: chosen, reason: "режим"}
A: {value: 10.0, unit: mm, provenance: chosen, reason: "—"}
B: {value: 20.0, unit: mm, provenance: chosen, reason: "—"}
'''


def _branching(tmp_path: Path) -> Path:
    root = tmp_path / "ветвь"
    (root / "parts").mkdir(parents=True)
    (root / "params").mkdir(parents=True)
    (root / "parts" / "p.py").write_text(BRANCH, encoding="utf-8")
    (root / "params" / "base.yaml").write_text(PARAMS, encoding="utf-8")
    return root


def test_branching_part_never_gets_a_stale_hit(tmp_path):
    """Параметр ветвления всегда среди прочитанных, поэтому входит в ключ."""
    root = _branching(tmp_path)
    a = Project.open(root).build(overrides={"MODE": 1.0})["ветвление"]
    b = Project.open(root).build(overrides={"MODE": 0.0})["ветвление"]
    assert a.topology.volume == pytest.approx(10 * 5 * 5)
    assert b.topology.volume == pytest.approx(20 * 5 * 5)
    assert not b.cached, "другая ветвь — другой ключ"

    a2 = Project.open(root).build(overrides={"MODE": 1.0})["ветвление"]
    assert a2.cached and a2.topology.volume == pytest.approx(250)


def test_changing_an_unread_parameter_still_hits(tmp_path):
    """При MODE=1 деталь читает A, но не B: правка B кеш не трогает."""
    root = _branching(tmp_path)
    Project.open(root).build(overrides={"MODE": 1.0})
    res = Project.open(root).build(overrides={"MODE": 1.0, "B": 99.0})["ветвление"]
    assert res.cached


# ------------------------------------------------------------------ недетерминированность
RANDOM_PART = '''
import random

from datum.build import P, part
from datum.geom import box


@part("случайная")
def build():
    return box(0, P.A + random.random(), 0, 5, 0, 5)
'''


def test_verify_mode_catches_a_nondeterministic_part(tmp_path):
    """Кеш молча отдаёт старое, если деталь собирается по-разному.

    Режим сверки пересобирает и сравнивает отпечатки — иначе такое живёт
    незамеченным годами.
    """
    root = _branching(tmp_path)
    (root / "parts" / "p.py").write_text(RANDOM_PART, encoding="utf-8")
    (root / "datum.toml").write_text(
        'schema = 1\nname = "случай"\n\n[cache]\nverify = true\n', encoding="utf-8")

    Project.open(root).build()
    with pytest.raises(ProjectError, match="детерминирован"):
        Project.open(root).build()


def test_verify_mode_answers_like_the_cache_when_the_part_agrees(cube):
    """Совпавшая пересборка не подменяет ответ: числа — сохранённые, как без
    сверки, иначе включённая сверка меняла бы снимки шумом float32."""
    toml = cube / "datum.toml"
    toml.write_text(toml.read_text(encoding="utf-8") + "\n[cache]\nverify = true\n",
                    encoding="utf-8")
    first = Project.open(cube).build(["кубик"])["кубик"]
    again = Project.open(cube).build(["кубик"])["кубик"]
    assert again.cached and again.topology.volume == first.topology.volume


def test_two_processes_storing_the_same_object_do_not_collide(tmp_path):
    """Найдено обзором: общий временный файл `<объект>.tmp` у двух процессов,
    кладущих один объект (окно и агент собирают одну деталь), — 10 отказов
    FileNotFoundError из 10. Теперь у каждого писателя свой временный файл."""
    import subprocess
    import sys

    code = ("import sys\n"
            "from datum.build.cache import BuildCache\n"
            "data = bytes(range(256)) * (40 * 4096)\n"
            "for _ in range(5):\n"
            "    c = BuildCache(sys.argv[1]); c.put_blob(data, 'mesh'); c.close()\n")
    for trial in range(5):
        root = tmp_path / f"п{trial}"
        root.mkdir()
        procs = [subprocess.Popen([sys.executable, "-c", code, str(root)],
                                  stderr=subprocess.PIPE, text=True) for _ in range(2)]
        errors = [p.communicate(timeout=120)[1] for p in procs]
        assert all(p.returncode == 0 for p in procs), errors
        objects = [f for f in root.rglob("*") if f.is_file()
                   and f.suffix == ".mesh"]
        assert len(objects) == 1 and objects[0].stat().st_size == 256 * 40 * 4096
        assert not [f for f in root.rglob("*.tmp")]
