"""ИИ-критик: когда звать, сколько кругов, что записано.

Критик — субагент плагина (`plugin/agents/critic.md`) в ЧИСТОМ контексте:
разговора главного агента он не видит и смотрит на проект заново — файлы,
проверки, сечения, виды. Его задача — найти настоящие недочёты и не уверенные
места и каждое обосновать числом или картинкой. Итог он записывает операцией
`critic_verdict` (сюда, в `.datum/critic.json`).

КОГДА. Главный агент зовёт критика сам, когда считает проект законченным
(правило навыка). Страховка — Stop-хук плагина (`gate_stop`): если проект
ВЫГРУЖЕН (`export`/`slice` писали output/ после последней правки конструкции),
а критик эту конструкцию ещё не видел, закончить ход нельзя — сначала критик.
Этапы работы («покажу part_view и спрошу») хук не трогает: выгрузки там нет.

СКОЛЬКО. Не больше ДВУХ кругов доработки на одно сообщение человека (решение
владельца): критик → доработка → критик → доработка — и всё; оставшиеся
замечания передаются человеку. Счётчик сбрасывается новым сообщением человека
(`gate_prompt`). Сверх того хук не блокирует больше `MAX_BLOCKS` раз за задачу,
что бы ни делал агент: бесконечного цикла не бывает по построению.

ЧТО ТАКОЕ «КОНСТРУКЦИЯ». Отпечаток файлов, которые её задают: манифест,
параметры, детали, `lib/`, проверки, сборка, положение, описи импорта. Правка
NOTES.md или выгрузка в output/ конструкцию не меняют.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

__all__ = ["fingerprint", "gate_stop", "gate_prompt", "is_user_prompt", "record", "load_state",
           "MAX_REVIEWS", "MAX_BLOCKS", "STATE_FILE"]

STATE_FILE = Path(".datum") / "critic.json"
#: Сколько раз критик зовётся за задачу: первый взгляд и проверка доработки.
#: Доработок при этом две — после каждого взгляда (решение владельца).
MAX_REVIEWS = 2
#: Сколько раз хук может не дать закончить за задачу — потолок на любой случай.
MAX_BLOCKS = 3

Verdict = Literal["принято", "доработать"]

#: Что задаёт конструкцию. Шаблоны — от корня проекта.
DESIGN = ("datum.toml", "assembly.yaml", "placement.yaml", "params/*.yaml",
          "parts/**/*.py", "lib/**/*.py", "checks/*.yaml", "checks/*.py",
          "imported/*/meta.json")
#: Что пишет выгрузка.
OUTPUT = ("output/*.3mf", "output/*.stl", "output/*.gcode")


def _files(root: Path, patterns) -> list[Path]:
    found = set()
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file() and "__pycache__" not in path.parts:
                found.add(path)
    return sorted(found)


def fingerprint(root: str | Path) -> str:
    """Отпечаток конструкции — по СОДЕРЖИМОМУ файлов (а не по времени: сохранение
    без правки — не правка)."""
    root = Path(root)
    h = hashlib.sha256()
    for path in _files(root, DESIGN):
        h.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        h.update(path.read_bytes() + b"\0")
    return h.hexdigest()[:16]


def _exported_after_design(root: Path, since: str | None = None) -> bool:
    """Выгружено ли то, что сейчас в проекте: файлы output/ новее правок
    конструкции — И выгрузка сделана в ЭТОЙ задаче (`since` — её начало).

    Без второго условия хук блокировал ходы, с выгрузкой не связанные: проект,
    выгруженный месяц назад, на вопрос «какая толщина стенки?» получал
    «запустите критика» до трёх раз, а «спасибо» после двух кругов — третий
    круг (аудит 2026-11-21, воспроизведено).
    """
    out = [p.stat().st_mtime for p in _files(root, OUTPUT)]
    design = [p.stat().st_mtime for p in _files(root, DESIGN)]
    if not out or max(out) < max(design, default=0.0):
        return False
    if since:
        try:
            started = datetime.fromisoformat(since).timestamp()
        except ValueError:
            return True
        # Запас в секунду: время начала задачи записано с точностью до секунды.
        return max(out) >= started - 1.0
    return True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state(root: str | Path) -> dict:
    try:
        state = json.loads((Path(root) / STATE_FILE).read_text(encoding="utf-8"))
        if isinstance(state, dict):
            return state
    except (OSError, ValueError):
        pass
    return {}


def _save(root: Path, state: dict) -> None:
    path = root / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    # Свой временный файл у каждого писателя: хук и `critic_verdict` пишут из
    # разных процессов, и общий `critic.tmp` один из них терял.
    from datum.doc.params_edit import write_atomically

    write_atomically(path, json.dumps(state, ensure_ascii=False, indent=2))


def _task(state: dict) -> dict:
    task = state.get("task")
    if not isinstance(task, dict):
        task = {"reviews": 0, "blocks": 0, "started": _now()}
        state["task"] = task
    return task


def _is_project(root: Path) -> bool:
    return (root / "datum.toml").is_file() and any((root / "parts").glob("*.py"))


#: Целиком из такого элемента — всё равно сообщение человека: вставленный текст
#: Claude Code заворачивает в `<pasted_content …>` (документация хуков).
_USER_WRAPPERS = frozenset({"pasted_content"})


def is_user_prompt(prompt: str) -> bool:
    """Написал ли это человек, а не сам Claude Code.

    `UserPromptSubmit` срабатывает не только на сообщения человека: и когда
    фоновый агент докладывает о завершении (документировано; замерено на
    настоящем CLI 2.x — `prompt` ровно `<task-notification>…</task-notification>`).
    Считай мы это ходом человека, критик, запущенный в фоне, обнулял бы свой
    же предел кругов, и защита от бесконечного цикла не работала бы (ловушка
    найдена в Kadr). Машинное — сообщение, целиком, от первого знака до
    последнего, состоящее из ОДНОГО элемента; текст, лишь начинающийся с тега
    или упоминающий его, — человека.
    """
    import re

    text = (prompt or "").strip()
    found = re.match(r"<([A-Za-z][\w-]*)[\s>]", text)
    if not found or found.group(1) in _USER_WRAPPERS:
        return True
    return not text.endswith(f"</{found.group(1)}>")


def gate_prompt(root: str | Path, prompt: str | None = None) -> None:
    """Новое сообщение человека — новая задача: круги считаются заново.
    Машинное (доклад фонового агента) — не новая задача (`is_user_prompt`)."""
    root = Path(root)
    if not (root / "datum.toml").is_file():
        return
    if prompt is not None and not is_user_prompt(prompt):
        return
    state = load_state(root)
    state["task"] = {"reviews": 0, "blocks": 0, "started": _now()}
    _save(root, state)


REASON_FIRST = (
    "Проект выгружен, а критик его ещё не видел. Прежде чем докладывать человеку, "
    "запустите субагента datum:critic (инструмент Agent/Task) — он посмотрит на проект "
    "заново, без нашего разговора, и запишет вердикт операцией critic_verdict. "
    "Замечания, с которыми согласны, — доработайте; спорные — передайте человеку с "
    "доводами обеих сторон.")
REASON_AGAIN = (
    "Критик просил доработать, и конструкция с тех пор изменилась. Запустите субагента "
    "datum:critic ещё раз — это второй и последний его круг на эту задачу.")


def _previous_remarks(last: dict) -> str:
    """Замечания прошлого круга — в указание: второй круг идёт в чистом
    контексте и иначе судил бы заново, а не проверял доработку."""
    remarks = [str(r) for r in (last.get("remarks") or []) if str(r).strip()]
    if not remarks:
        return ""
    shown = "; ".join(f"{i}) {r}" for i, r in enumerate(remarks[:12], 1))
    return (" Передайте критику замечания первого круга и попросите начать с проверки, "
            "устранены ли они: " + shown)


def gate_stop(root: str | Path) -> dict | None:
    """Решение Stop-хука: None — можно заканчивать; иначе — {"decision": "block",
    "reason": …}, и агент продолжает с этим указанием."""
    root = Path(root)
    if not _is_project(root):
        return None
    state = load_state(root)
    task = _task(state)
    raw = state.get("last")
    last: dict = raw if isinstance(raw, dict) else {}
    current = fingerprint(root)
    if last.get("fingerprint") == current or int(task.get("blocks", 0)) >= MAX_BLOCKS:
        return None
    reviews = int(task.get("reviews", 0))
    if reviews == 0 and _exported_after_design(root, task.get("started")):
        reason = REASON_FIRST
    elif 0 < reviews < MAX_REVIEWS and last.get("verdict") == "доработать":
        reason = REASON_AGAIN + _previous_remarks(last)
    else:
        return None
    task["blocks"] = int(task.get("blocks", 0)) + 1
    _save(root, state)
    return {"decision": "block", "reason": reason}


def record(root: str | Path, verdict: Verdict, remarks: list[str], summary: str = "") -> dict:
    """Записать вердикт критика; вернуть, какой это круг и что делать дальше."""
    root = Path(root)
    state = load_state(root)
    task = _task(state)
    task["reviews"] = int(task.get("reviews", 0)) + 1
    review = task["reviews"]
    state["last"] = {"fingerprint": fingerprint(root), "verdict": verdict,
                     "remarks": list(remarks), "summary": summary, "at": _now(),
                     "review": review}
    _save(root, state)
    if verdict == "принято":
        advice = "Критик принял конструкцию — можно докладывать человеку."
    elif review < MAX_REVIEWS:
        advice = (f"Круг {review} из {MAX_REVIEWS}: доработайте по замечаниям, с которыми "
                  f"согласны; с чем не согласны — передайте человеку с доводами. После "
                  f"доработки критик посмотрит ещё раз.")
    else:
        advice = (f"Круг {review} из {MAX_REVIEWS} — последний: исправьте бесспорное, "
                  f"остальные замечания передайте человеку как есть. Критик на эту задачу "
                  f"больше не зовётся.")
    return {"review": review, "max_reviews": MAX_REVIEWS, "advice": advice}
