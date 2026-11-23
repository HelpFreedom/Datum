"""Приспособления для процессных тестов.

Здесь проверяется то, что на `TestClient` не поймать: как сервер живёт и
умирает как ОТДЕЛЬНЫЙ ПРОЦЕСС. Оба закреплённых здесь отказа стоили
двадцати семи осиротевших серверов, накопившихся за один вечер прогонов.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
LATCH = ROOT / "examples" / "latch"
GENERATED = shutil.ignore_patterns(".datum", "snapshots", "__pycache__", "output")


@pytest.fixture
def latch(tmp_path) -> Path:
    shutil.copytree(LATCH, tmp_path / "latch", ignore=GENERATED)
    return tmp_path / "latch"
