"""Что изменилось с прошлого прогона проверок.

Найдено разбором сессий агента: после почти каждого `check_run` шла команда
«Summarise check results» — свой Python, сравнивающий новый ответ с прежним.
Сравнение нужно всем (и человеку: «что я сломал этой правкой?»), поэтому его
делает сам прогон: вердикт и запас каждой проверки запоминаются в
`.datum/last-checks.json`, и следующий прогон называет перемены.

Полный прогон заменяет запись целиком (исчезнувшие проверки уходят),
выборочный — обновляет только то, что считал. Подстановки параметров
(`overrides`) сюда не попадают: это не состояние проекта.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from datum.checks.result import CheckRun

__all__ = ["FILE", "changes", "remember"]

FILE = "last-checks.json"

_BAD = ("fail", "error")
_WORD = {"pass": "прошла", "fail": "FAIL", "error": "ошибка", "warn": "ВНИМ",
         "skip": "пропуск"}


def _path(root: Path) -> Path:
    return Path(root) / ".datum" / FILE


def _load(root: Path) -> dict[str, dict]:
    try:
        data = json.loads(_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    checks = data.get("checks") if isinstance(data, dict) else None
    return checks if isinstance(checks, dict) else {}


def changes(root: Path, run: CheckRun) -> dict:
    """Перемены относительно запомненного: словарь списков id и строки отчёта.

    Пустой словарь `lines` — либо прошлого прогона нет, либо ничего не
    изменилось; различает их ключ `known`.
    """
    before = _load(root)
    worse: list[str] = []
    better: list[str] = []
    new: list[str] = []
    moved: list[str] = []
    for r in run.results:
        old = before.get(r.id)
        if old is None:
            if before:
                new.append(r.id)
            continue
        was, now = old.get("status"), r.status
        if was == now:
            continue
        if now in _BAD and was not in _BAD:
            worse.append(f"{r.id} ({_WORD.get(was, was)} → {_WORD.get(now, now)})")
        elif was in _BAD and now not in _BAD:
            better.append(f"{r.id} ({_WORD.get(was, was)} → {_WORD.get(now, now)})")
        else:
            moved.append(f"{r.id} ({_WORD.get(was, was)} → {_WORD.get(now, now)})")
    lines: list[str] = []
    if worse:
        lines.append("  стало хуже: " + "; ".join(worse))
    if better:
        lines.append("  исправлено: " + "; ".join(better))
    if moved:
        lines.append("  изменился вердикт: " + "; ".join(moved))
    if new:
        lines.append("  новые проверки: " + ", ".join(new))
    return {"known": bool(before), "worse": worse, "better": better, "moved": moved,
            "new": new, "lines": lines}


def remember(root: Path, run: CheckRun, *, full: bool) -> None:
    """Запомнить вердикты. Не удалось записать — не беда: это память, а не итог."""
    kept = {} if full else _load(root)
    for r in run.results:
        kept[r.id] = {"status": r.status, "margin": r.margin}
        if r.status != "pass":
            # Сводке проекта (`doc/brief.py`) — что именно не так, без прогона.
            kept[r.id]["message"] = str(r.message)[:300]
    path = _path(root)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"checks": kept}, ensure_ascii=False, default=str),
                       encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
