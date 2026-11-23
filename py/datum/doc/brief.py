"""Сводка проекта для чистого контекста.

Отвечает на вопрос новой сессии «что это и на чём остановились» — в пределах
нескольких тысяч знаков и БЕЗ исполнения кода проекта: читаются файлы, а не
открывается проект. Поэтому её может печатать хук плагина при старте сессии и
после сжатия контекста (за доли секунды), и операция не требует изоляции.

Сводка — указатель, а не замена инструментам: числа проверок в ней — из
ПРОШЛОГО прогона (`.datum/last-checks.json`), и она это говорит.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import tomllib
from pathlib import Path

from datum.doc import journal

__all__ = ["compose", "MAX_CHARS"]

#: Предел сводки. Хук может добавить в контекст до 10 000 знаков; держимся ниже.
MAX_CHARS = 6000

_PART = re.compile(r"@part\(\s*[\"']([^\"']+)[\"']([^)]*)\)")


def _age(seconds: float) -> str:
    if seconds < 90:
        return "только что"
    if seconds < 5400:
        return f"{seconds / 60:.0f} мин назад"
    if seconds < 129600:
        return f"{seconds / 3600:.0f} ч назад"
    return f"{seconds / 86400:.0f} дн. назад"


def _parts(root: Path) -> list[tuple[str, bool]]:
    """(имя, печатается ли) — по объявлениям `@part(...)` в parts/*.py, текстом."""
    found: list[tuple[str, bool]] = []
    for path in sorted((root / "parts").glob("*.py")) if (root / "parts").is_dir() else []:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in _PART.finditer(text):
            found.append((m.group(1), "printed=False" not in m.group(2).replace(" ", "")))
    return found


def _params(root: Path) -> list[str]:
    from datum.doc.params import ParamError, ParamRegistry

    path = root / "params" / "base.yaml"
    if not path.is_file():
        return ["параметров нет (params/base.yaml)"]
    try:
        reg = ParamRegistry.load(path)
    except (ParamError, OSError, ValueError) as e:
        return [f"params/base.yaml не читается: {str(e)[:200]}"]
    items = list(reg)
    unknown = list(reg.unknowns)
    blocking = [p.name for p in unknown if p.blocking]
    lines = [f"параметров {len(items)}, незамеренных догадок {len(unknown)}"]
    if blocking:
        lines.append("БЛОКИРУЮТ печать (assumed + blocking): " + ", ".join(blocking[:20])
                     + (" …" if len(blocking) > 20 else ""))
    return lines


def _checks(root: Path, now: float) -> list[str]:
    path = root / ".datum" / "last-checks.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        checks = data["checks"]
        age = _age(now - path.stat().st_mtime)
    except (OSError, ValueError, KeyError, TypeError):
        return ["проверки в этом проекте ещё не прогонялись (или прогон не запомнен) — "
                "check_run"]
    bad = {k: v for k, v in checks.items() if v.get("status") in ("fail", "error")}
    warn = [k for k, v in checks.items() if v.get("status") == "warn"]
    lines = [f"прошлый прогон ({age}): всего {len(checks)}, не прошло {len(bad)}, "
             f"предупреждений {len(warn)} — это ПРОШЛЫЙ прогон, свежие числа даёт check_run"]
    for cid, v in list(bad.items())[:12]:
        message = str(v.get("message") or "")[:160]
        lines.append(f"  НЕ ПРОШЛА {cid}" + (f": {message}" if message else ""))
    if len(bad) > 12:
        lines.append(f"  … и ещё {len(bad) - 12}")
    if warn:
        lines.append("  предупреждения: " + ", ".join(warn[:10]) + (" …" if len(warn) > 10 else ""))
    return lines


def _output(root: Path, now: float) -> list[str]:
    from datum.doc import critic

    out = critic._files(root, critic.OUTPUT)
    lines = []
    if out:
        newest = max(p.stat().st_mtime for p in out)
        design = max((p.stat().st_mtime for p in critic._files(root, critic.DESIGN)), default=0.0)
        state = ("конструкция с тех пор менялась — выгрузка устарела" if design > newest
                 else "соответствует нынешней конструкции")
        lines.append(f"выгружено файлов {len(out)} ({_age(now - newest)}): {state}")
    else:
        lines.append("выгрузки нет (output/ пуст)")
    last = critic.load_state(root).get("last")
    if isinstance(last, dict) and last.get("verdict"):
        remarks = [str(r) for r in last.get("remarks") or []][:4]
        lines.append(f"критик: «{last['verdict']}»"
                     + (" — " + "; ".join(remarks) if remarks else ""))
    return lines


def compose(root: str | Path, *, now: dt.datetime | None = None, entries: int = 14) -> str:
    """Сводка текстом. Каталог без `datum.toml` — пустая строка."""
    root = Path(root)
    manifest_path = root / "datum.toml"
    if not manifest_path.is_file():
        return ""
    moment = (now or dt.datetime.now()).timestamp()
    try:
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        manifest = {}
    name = str(manifest.get("name") or root.name)
    printer = manifest.get("printer") or {}
    head = f"ПРОЕКТ DATUM «{name}» — {root}"
    if printer:
        head += (f"\nпечать: {printer.get('material', 'PLA')}, сопло "
                 f"{printer.get('nozzle', '?')}")
    parts = _parts(root)
    printed = [n for n, p in parts if p]
    bought = [n for n, p in parts if not p]
    sections = [head]
    line = f"ДЕТАЛИ ({len(parts)}): " + (", ".join(printed[:40]) or "нет")
    if len(printed) > 40:
        line += " …"
    if bought:
        line += f"\n  не печатаются (покупное, показное): {', '.join(bought[:20])}"
    sections.append(line)
    sections.append("ПАРАМЕТРЫ: " + "\n  ".join(_params(root)))
    sections.append("ПРОВЕРКИ: " + "\n".join(_checks(root, moment)))
    sections.append("ВЫГРУЗКА: " + "\n  ".join(_output(root, moment)))
    log = journal.read(root)
    if log:
        shown = log[-entries:]
        sections.append(f"ЖУРНАЛ РЕШЕНИЙ (последние {len(shown)} из {len(log)}, "
                        f"{journal.FILE}):\n" + "\n".join(e.line() for e in shown))
    else:
        sections.append(f"ЖУРНАЛ РЕШЕНИЙ пуст ({journal.FILE}) — записывайте решения, "
                        f"отказы от вариантов, замеры и итоги печати операцией note_add")
    # Библиотека покупного — общая на все проекты: новая сессия должна знать,
    # что мотор уже обмерен, прежде чем просить человека мерить его заново.
    try:
        from datum.doc import components

        shelf = [c["name"] for c in components.listing() if not c.get("broken")]
    except Exception:                                # noqa: BLE001 — сводка обязана выйти
        shelf = []
    if shelf:
        sections.append(f"БИБЛИОТЕКА ПОКУПНОГО ({len(shelf)}): " + ", ".join(shelf[:30])
                        + (" …" if len(shelf) > 30 else "")
                        + " — component_list, взять в проект: component_use")
    # Опыт печати — тоже общий: что уже выходило плохо на этом принтере.
    try:
        from datum.doc import lessons

        learnt = lessons.read()
    except Exception:                                # noqa: BLE001
        learnt = []
    if learnt:
        sections.append(f"ОПЫТ ПЕЧАТИ (общая база, последние {min(len(learnt), 6)} из "
                        f"{len(learnt)}; все — lessons_list, записать — print_log):\n"
                        + "\n".join("  " + x.line() for x in learnt[-6:]))
    notes = root / "NOTES.md"
    if notes.is_file():
        try:
            n = sum(1 for _ in notes.open(encoding="utf-8"))
        except OSError:
            n = 0
        sections.append(f"NOTES.md — {n} строк подробных заметок; читать по надобности")
    text = "\n\n".join(sections)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS - 60].rstrip() + "\n… (сводка обрезана; подробности — инструментами)"
    return text
