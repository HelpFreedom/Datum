"""Запись кеша сверяется с тем, какой она читается, а негодная — не молчит.

Найдено на Gear Cube (аудит 2026-11-21): составная деталь «сборка» после
записи во float32 читалась с другим числом тел и полостей (7 и 3 → 8 и 6),
сверка отвергала запись молча, и 339 тыс. треугольников пересобирались 24 с
при каждом открытии проекта — навсегда.
"""

from __future__ import annotations

import json

from datum.doc.project import Project


def _row(pr: Project, part: str = "кубик"):
    return pr.cache._db.execute(
        "SELECT key, digest, metrics FROM builds WHERE part=?", (part,)).fetchone()


def _set_metrics(pr: Project, key: str, metrics: dict) -> None:
    pr.cache._db.execute("UPDATE builds SET metrics=? WHERE key=?",
                         (json.dumps(metrics), key))
    pr.cache._db.commit()


def test_fresh_build_records_how_the_mesh_reads_back(cube):
    pr = Project.open(cube)
    first = pr.build()["кубик"]
    assert first.ok and not first.cached and first.note == ""
    metrics = json.loads(_row(pr)[2])
    assert metrics["stored"] == {"bodies": 1, "sealed_voids": 0,
                                 "triangles": first.topology.triangles}
    again = Project.open(cube).build()["кубик"]
    assert again.cached and again.note == ""


def test_hit_is_judged_against_the_stored_reading_not_the_fresh_numbers(cube):
    """Ровно случай «сборки»: числа свежей сборки расходятся с прочитанным,
    а запись годна — она читается такой, какой записана."""
    pr = Project.open(cube)
    pr.build()
    key, _, raw = _row(pr)
    metrics = json.loads(raw)
    metrics["bodies"], metrics["sealed_voids"] = 7, 3      # как сказала свежая сборка
    metrics["body_volumes_mm3"] = [1.0] * 7
    _set_metrics(pr, key, metrics)
    res = Project.open(cube).build()["кубик"]
    assert res.cached, "запись, читающаяся как записана, обязана годиться"
    assert res.topology.bodies == 7, "человеку — числа свежей сборки, кеш их не меняет"


def test_a_record_that_reads_differently_is_rebuilt_said_aloud_and_replaced(cube):
    pr = Project.open(cube)
    pr.build()
    key, _, raw = _row(pr)
    metrics = json.loads(raw)
    metrics["stored"]["bodies"] = 5
    _set_metrics(pr, key, metrics)
    res = Project.open(cube).build()["кубик"]
    assert res.ok and not res.cached
    assert "запись кеша была негодной: записано тел 5" in res.note
    assert "запись кеша была негодной" in res.line()
    assert res.as_dict()["note"] == res.note
    # Пересборка ЗАМЕНЯЕТ запись под тем же ключом — следующая сборка из кеша.
    # Прежде обновлялось только время, и негодная запись жила вечно.
    after = Project.open(cube).build()["кубик"]
    assert after.cached and after.note == ""


def test_a_legacy_record_without_the_stored_reading_still_works(cube):
    pr = Project.open(cube)
    pr.build()
    key, _, raw = _row(pr)
    metrics = json.loads(raw)
    del metrics["stored"]
    _set_metrics(pr, key, metrics)
    assert Project.open(cube).build()["кубик"].cached
    metrics["bodies"] = 3                        # прежний вид и не сходится — негодна
    _set_metrics(pr, key, metrics)
    res = Project.open(cube).build()["кубик"]
    assert not res.cached and "читается 1 и 0" in res.note


def test_a_truncated_object_is_a_rebuild_not_a_crash(cube):
    """Обрыв питания посреди записи: файл есть, но урезан. Прежде ValueError
    чтения не ловился, и деталь не собиралась до ручной чистки кеша."""
    pr = Project.open(cube)
    pr.build()
    _, digest, _ = _row(pr)
    blob = pr.cache.blob_path(digest, "mesh")
    blob.write_bytes(blob.read_bytes()[:40])
    res = Project.open(cube).build()["кубик"]
    assert res.ok and not res.cached and "не читается" in res.note


def test_cache_keeps_its_budget_by_itself(cube):
    """Чистила кеш только операция cache_gc, которую никто не звал."""
    pr = Project.open(cube)
    pr.build()
    junk = [pr.cache.put_blob(bytes([i]) * 400_000, "png", kind="evidence") for i in range(4)]
    assert all(pr.cache.blob_path(d, "png") for d in junk)
    pr.cache._db.execute("UPDATE objects SET atime=1 WHERE ext='png'")
    pr.cache._db.commit()
    pr.cache.budget_mb = 1
    assert pr.cache.over_budget()
    # Новая сборка (другое значение параметра — другая запись) запускает чистку.
    size = next(p.name for p in pr.params if p.provenance != "derived" and p.unit == "mm")
    pr.build(overrides={size: pr.params[size].display + 1.0})
    assert not pr.cache.over_budget()
    assert sum(1 for d in junk if pr.cache.blob_path(d, "png")) < 4, "уходит давно не читанное"
    assert Project.open(cube).build()["кубик"].ok


def test_a_data_file_read_by_the_part_is_in_the_cache_key(cube):
    """Перила советуют «положите данные в проект и читайте из файла», а файл в
    ключ не входил: правка таблицы отдавала вчерашнюю геометрию."""
    (cube / "data").mkdir()
    (cube / "data" / "size.txt").write_text("12", encoding="utf-8")
    (cube / "parts" / "cube.py").write_text(
        "from pathlib import Path\n"
        "from datum.build import part\n"
        "from datum.geom import box\n\n\n"
        "@part('кубик')\n"
        "def cube():\n"
        "    side = float((Path(__file__).parent.parent / 'data' / 'size.txt').read_text())\n"
        "    return box(100, 100 + side, 100, 100 + side, 0, side)\n", encoding="utf-8")
    first = Project.open(cube).build()["кубик"]
    assert first.ok and first.topology.volume == 12 ** 3
    assert Project.open(cube).build()["кубик"].cached
    (cube / "data" / "size.txt").write_text("15", encoding="utf-8")
    after = Project.open(cube).build()["кубик"]
    assert not after.cached and after.topology.volume == 15 ** 3
    assert Project.open(cube).build()["кубик"].cached
