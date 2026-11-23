"""Журнал решений и сводка проекта для новой сессии.

Разбор сессий (аудит 2026-11-21): новая сессия восстанавливала картину чтением
старых журналов разговоров, поэтому сессии вели до упора окна контекста, и 71 %
расхода уходил на его повторное чтение.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import subprocess
from pathlib import Path

import pytest

from datum.doc import brief, journal
from datum.tools import ops, ops_params

REPO = Path(__file__).resolve().parents[3]


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ журнал
def test_journal_is_one_line_per_entry_and_survives_hand_edits(tmp_path):
    when = dt.datetime(2026, 11, 22, 14, 5)
    first = journal.add(tmp_path, "решение", "Стенка 2.4:\n  при 1.6 язычок ломался", now=when)
    assert first.line() == "- 2026-11-22 14:05 [решение] Стенка 2.4: при 1.6 язычок ломался"
    journal.add(tmp_path, "печать", "крышка: вершиной вниз рёбра оплыли", now=when)
    path = tmp_path / journal.FILE
    # Человек дописал своё и строку без времени — журнал читается дальше.
    path.write_text(path.read_text("utf-8") + "\nзаметка от руки\n"
                    "- 2026-11-23 [замер] LIP_D = 0.92 штангенциркулем\n", encoding="utf-8")
    entries = journal.read(tmp_path)
    assert [e.kind for e in entries] == ["решение", "печать", "замер"]
    assert entries[2].time == "" and entries[2].text.startswith("LIP_D")
    assert path.read_text("utf-8").startswith("# Журнал решений")


def test_journal_refuses_what_is_not_a_line(tmp_path):
    with pytest.raises(journal.JournalError, match="род записи"):
        journal.add(tmp_path, "мысль", "x")
    with pytest.raises(journal.JournalError, match="пустая"):
        journal.add(tmp_path, "решение", "  \n ")
    with pytest.raises(journal.JournalError, match="длиннее"):
        journal.add(tmp_path, "решение", "я" * 601)
    assert not (tmp_path / journal.FILE).exists()


# ------------------------------------------------------------------ сводка
def test_brief_tells_a_fresh_session_where_things_stand(cube):
    ops.clear_project_cache()
    _run(ops.check_run(str(cube)))
    _run(ops_params.note_add(str(cube), "решение", "Кубик 20 мм: под калибровку стола"))
    text = brief.compose(cube)
    assert "ДЕТАЛИ (1): кубик" in text
    assert "прошлый прогон" in text and "это ПРОШЛЫЙ прогон" in text
    assert "[решение] Кубик 20 мм: под калибровку стола" in text
    assert "выгрузки нет" in text
    assert len(text) <= brief.MAX_CHARS
    out = _run(ops_params.project_brief(str(cube)))
    assert out["report"] == brief.compose(cube) and out["chars"] == len(text)


def test_brief_names_what_failed_last_time_without_running_anything(cube):
    checks = cube / "checks" / "checks.yaml"
    checks.write_text(checks.read_text("utf-8")
                      + '\n- id: кубик.тесно\n  use: min_wall\n'
                        '  args: {part: "@кубик", wall: 100.0}\n', encoding="utf-8")
    ops.clear_project_cache()
    run = _run(ops.check_run(str(cube)))
    assert not run["ok"], run["report"]
    ops.clear_project_cache()
    # Сводка читает файлы: сломанный скрипт детали ей не мешает.
    (cube / "parts" / "cube.py").write_text("это не питон (", encoding="utf-8")
    text = brief.compose(cube)
    assert "НЕ ПРОШЛА кубик.тесно" in text and "не прошло 1" in text


def test_brief_is_bounded_and_empty_outside_a_project(tmp_path, cube):
    assert brief.compose(tmp_path) == ""
    for i in range(400):
        journal.add(cube, "решение", f"запись {i} " + "длинный текст " * 30)
    assert len(brief.compose(cube)) <= brief.MAX_CHARS


def test_brief_counts_parts_that_are_not_printed(cube):
    (cube / "parts" / "bought.py").write_text(
        'from datum.build import part\nfrom datum.geom import box\n\n\n'
        '@part("мотор", printed=False)\ndef motor():\n    return box(0, 1, 0, 1, 0, 1)\n',
        encoding="utf-8")
    text = brief.compose(cube)
    assert "ДЕТАЛИ (2): кубик" in text and "не печатаются (покупное, показное): мотор" in text


# ------------------------------------------------------------------ хук
def test_the_session_hook_puts_the_brief_into_context(cube):
    hook = REPO / "plugin" / "hooks" / "session-brief"
    assert os.access(hook, os.X_OK)
    out = subprocess.run([str(hook)], input=json.dumps({"cwd": str(cube), "source": "compact"}),
                         capture_output=True, text=True, timeout=60,
                         env={**os.environ, "CLAUDE_PROJECT_DIR": ""})
    assert out.returncode == 0, out.stderr
    reply = json.loads(out.stdout)["hookSpecificOutput"]
    assert reply["hookEventName"] == "SessionStart"
    context = reply["additionalContext"]
    assert context.startswith("Контекст только что сжат") and "ДЕТАЛИ (1): кубик" in context
    assert len(context) < 10_000, "больше — Claude Code уводит добавку в файл"
    # Вне проекта хук молчит.
    quiet = subprocess.run([str(hook)], input=json.dumps({"cwd": str(cube.parent)}),
                           capture_output=True, text=True, timeout=60,
                           env={**os.environ, "CLAUDE_PROJECT_DIR": ""})
    assert quiet.returncode == 0 and quiet.stdout.strip() == ""


def test_the_plugin_declares_the_session_hook():
    hooks = json.loads((REPO / "plugin" / "hooks" / "hooks.json").read_text("utf-8"))["hooks"]
    assert "session-brief" in hooks["SessionStart"][0]["hooks"][0]["command"]
    assert {"Stop", "UserPromptSubmit", "SessionStart"} <= set(hooks)
