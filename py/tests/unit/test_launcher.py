"""Лаунчер `scripts/datum`: журнал окна не растёт без конца.

Журнал (`~/.local/state/datum/window.log`) дописывался каждым запуском и рос
без предела. Теперь больший LOG_MAX уезжает в window.1.log при запуске (тот —
в window.2.log): журнал упавшего запуска переживает следующий.

Окно здесь НЕ открывается: `setsid` в PATH подменён записью аргументов.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = ROOT / "scripts" / "datum"
BUILT = (ROOT / "apps" / "shell" / "dist" / "main" / "main.js").is_file() and \
    (ROOT / "node_modules" / "electron" / "dist" / "electron").is_file()

pytestmark = pytest.mark.skipif(not BUILT, reason="окно не собрано — лаунчер откажет раньше")


def _launch(tmp_path: Path) -> Path:
    fake = tmp_path / "bin"
    fake.mkdir(exist_ok=True)
    record = tmp_path / "setsid.args"
    (fake / "setsid").write_text(f'#!/usr/bin/env bash\necho "$@" > "{record}"\n',
                                 encoding="utf-8")
    (fake / "setsid").chmod(0o755)
    env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}",
           "XDG_STATE_HOME": str(tmp_path / "state")}
    subprocess.run([str(LAUNCHER)], env=env, check=True, capture_output=True,
                   stdin=subprocess.DEVNULL, timeout=60)
    assert record.is_file(), "лаунчер не дошёл до запуска окна"
    return tmp_path / "state" / "datum"


def test_a_big_log_is_rotated_and_the_previous_one_kept(tmp_path):
    state = tmp_path / "state" / "datum"
    state.mkdir(parents=True)
    (state / "window.log").write_bytes(b"x" * (4 * 1024 * 1024 + 1))
    (state / "window.1.log").write_text("прошлый запуск\n", encoding="utf-8")

    _launch(tmp_path)

    assert (state / "window.2.log").read_text(encoding="utf-8") == "прошлый запуск\n"
    assert (state / "window.1.log").stat().st_size == 4 * 1024 * 1024 + 1
    fresh = (state / "window.log").read_text(encoding="utf-8")
    assert fresh.startswith("\n=== ") and len(fresh) < 1000


def test_a_small_log_keeps_growing_in_place(tmp_path):
    state = tmp_path / "state" / "datum"
    state.mkdir(parents=True)
    (state / "window.log").write_text("прошлый запуск\n", encoding="utf-8")

    _launch(tmp_path)

    text = (state / "window.log").read_text(encoding="utf-8")
    assert text.startswith("прошлый запуск\n") and "\n=== " in text
    assert not (state / "window.1.log").exists()
