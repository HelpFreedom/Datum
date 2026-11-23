"""Что изменилось в форме между версиями — картинкой «было / стало».

Идея — из cadcraft (COMPARE красит различия чертежей). Первый вариант рисовал
одним листом и прятал убранное: у latch с выросшим корпусом старая крышка
оказалась внутри новых стенок, и красного не было видно вовсе, хотя убралось
5883 мм³. Поэтому листов два, в одном кадре.
"""

from __future__ import annotations

import asyncio
import io

import pytest
from PIL import Image

import datum.tools.ops  # noqa: F401  — объявление операций
from datum.doc.geomdiff import ADDED, COMMON, REMOVED, geometry_diff
from datum.doc.project import Project
from datum.doc.snapshot import GEOMETRY_DIR, Snapshot, kept_geometry
from datum.geom.measure import vol
from datum.tools.registry import OPS


@pytest.fixture
def pair(latch):
    pr = Project.open(latch)
    a = Snapshot.create(pr, "было")
    b = Snapshot.create(pr, "стало", overrides={"CASE_H": 24.0})
    a.save(latch)
    b.save(latch)
    return latch, a, b


def _colours(png: bytes) -> set[tuple[int, int, int]]:
    image = Image.open(io.BytesIO(png)).convert("RGB")
    return {c for _, c in image.getcolors(maxcolors=1 << 20)}


def _has(colours, hex_: str) -> bool:
    """Цвет есть на листе — с учётом затенения: свет умножает каналы на одно
    число, поэтому сравнивается ЦВЕТНОСТЬ (доли каналов), а тёмное — край,
    фон, текст — не в счёт."""
    target = [int(hex_[i:i + 2], 16) for i in (1, 3, 5)]
    t = [v / sum(target) for v in target]
    for c in colours:
        total = sum(c)
        if total < 200:
            continue
        if max(abs(c[k] / total - t[k]) for k in range(3)) < 0.03:
            return True
    return False


def test_the_snapshot_keeps_the_shape_of_every_part(pair):
    root, a, b = pair
    for snap in (a, b):
        for name, part in snap.parts.items():
            solid = kept_geometry(root, part.result_digest, name=name)
            assert solid is not None and vol(solid) == pytest.approx(part.volume_mm3, rel=1e-6)
    # Одинаковая форма в двух снимках лежит одним файлом.
    assert len(list((root / GEOMETRY_DIR).glob("*.mesh"))) == 4


def test_volumes_add_up_to_the_change(pair):
    root, a, b = pair
    g = geometry_diff(root, a, b)
    added, removed = g.volumes["корпус"]
    grown = b.parts["корпус"].volume_mm3 - a.parts["корпус"].volume_mm3
    assert added - removed == pytest.approx(grown, abs=0.01)
    assert added > 0 and removed > 0                 # губка ушла вверх: было и то и другое
    # Крышка 3 мм уехала на 4 мм вверх: старое и новое места не пересекаются.
    lid = a.parts["крышка"].volume_mm3
    assert g.volumes["крышка"] == (pytest.approx(lid, rel=1e-6), pytest.approx(lid, rel=1e-6))


def test_both_sheets_show_their_colour(pair):
    """«было» обязано показать красное, «стало» — зелёное: на одном листе
    красное пряталось за новым корпусом."""
    root, a, b = pair
    png = geometry_diff(root, a, b).png
    image = Image.open(io.BytesIO(png)).convert("RGB")
    top = image.crop((0, 0, image.width, image.height // 2)).tobytes()
    bottom = image.crop((0, image.height // 2, image.width, image.height)).tobytes()

    def colours(raw: bytes) -> set:
        return {tuple(raw[i:i + 3]) for i in range(0, len(raw), 3 * 7)}

    assert _has(colours(top), REMOVED) and not _has(colours(top), ADDED)
    assert _has(colours(bottom), ADDED) and not _has(colours(bottom), REMOVED)
    assert _has(_colours(png), COMMON)


def test_an_unchanged_project_is_all_common(latch):
    pr = Project.open(latch)
    a, b = Snapshot.create(pr, "раз"), Snapshot.create(pr, "два")
    g = geometry_diff(latch, a, b)
    assert sorted(g.unchanged) == ["корпус", "крышка"] and not g.volumes
    assert g.png is not None and "форма не изменилась" in "\n".join(g.lines())


def test_a_snapshot_without_shapes_says_so(pair):
    """Снимок, снятый до картинок (или проект без `.datum/`), — «формы нет»,
    а не пустая или ложная картинка."""
    root, a, b = pair
    for f in (root / GEOMETRY_DIR).glob("*.mesh"):
        f.unlink()
    g = geometry_diff(root, a, b)
    assert sorted(g.missing) == ["корпус", "крышка"] and g.png is None
    assert "формы нет" in "\n".join(g.lines())


def test_a_hand_edited_digest_never_becomes_a_path(latch):
    assert kept_geometry(latch, "../../datum.toml") is None
    assert kept_geometry(latch, "") is None


def test_the_operation_returns_the_picture_and_numbers(pair):
    root, _, _ = pair
    out = asyncio.run(OPS["snapshot_diff"].fn(project=str(root), image=True))
    assert not out.get("error"), out.get("error")
    assert out["image"]["url"].startswith("/obj/") and (root / out["image"]["path"]).is_file()
    assert out["geometry"]["volumes"]["корпус"]["added_mm3"] > 0
    assert "Форма:" in out["report"] and "картинка:" in out["report"]
    plain = asyncio.run(OPS["snapshot_diff"].fn(project=str(root)))
    assert "image" not in plain and "geometry" not in plain     # без image — как прежде
