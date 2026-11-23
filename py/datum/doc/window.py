"""Что сейчас в окне: выделение, скрытое, срез, камера, последний замер.

Агент в терминале видит проект, но не окно: «посмотри на выделенную» и
«проверь мой замер» были для него пустыми словами. Окно сообщает серверу, что
видит человек (операция `window_report`), и агент читает это одним вызовом
(`window_state`); вид стола «как в окне» (`scene_view`) берёт отсюда камеру и
скрытое.

Хранится в `.datum/window.json`, а не в памяти сервера: изолированные операции
идут в отдельном процессе, а агент бывает и без поднятого окна — тогда он
получает последнее известное с пометкой, что окно закрыто и сколько прошло.

Это СВЕДЕНИЯ, а не расчёт: числа замера здесь — то, что показало окно; мерит
по-прежнему сервер, и агент может перемерить их своими операциями. Пишет сюда
только окно — от агента сервер `window_report` не принимает, иначе «что видит
человек» можно было бы подделать.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
from contextvars import ContextVar
from pathlib import Path
from typing import Any

__all__ = ["FILE", "WindowStateError", "sanitize", "save", "load", "describe", "SERVING"]

#: Операция исполняется сервером окна: событие из её ответа дойдёт до окна.
#: Ставит `server/jobs.py` на время вызова. Вне сервера (CLI, свой процесс MCP)
#: — False, и `window_show` пересылает вызов живому серверу проекта сам.
SERVING: ContextVar[bool] = ContextVar("datum_serving_window", default=False)

FILE = Path(".datum") / "window.json"

#: Больше этого окно присылать не должно: там имена и десяток чисел.
MAX_BYTES = 64 * 1024

TOOLS = {"none", "distance", "probe", "move", "rotate"}
PLANES = {"xy", "xz", "yz"}
KINDS = {"point", "edge", "circle", "plane"}


class WindowStateError(ValueError):
    """Присланное окном не похоже на состояние окна."""


def _num(v: Any, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise WindowStateError(f"{what}: ожидалось конечное число")
    return float(v)


def _vec(v: Any, what: str) -> list[float]:
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        raise WindowStateError(f"{what}: ожидались три числа")
    return [round(_num(x, what), 4) for x in v]


def _name(v: Any, what: str) -> str:
    if not isinstance(v, str) or not v or len(v) > 200:
        raise WindowStateError(f"{what}: ожидалось имя")
    return v


def _names(v: Any, what: str) -> list[str]:
    if not isinstance(v, list) or len(v) > 1000:
        raise WindowStateError(f"{what}: ожидался список имён")
    return [_name(x, what) for x in v]


def _text(v: Any, what: str, limit: int = 2000) -> str:
    if not isinstance(v, str) or len(v) > limit:
        raise WindowStateError(f"{what}: ожидалась строка до {limit} знаков")
    return v


def _element(e: Any) -> dict:
    """Выбранный штангенциркулем элемент — в мировых координатах."""
    if not isinstance(e, dict) or e.get("type") not in KINDS:
        raise WindowStateError("picked: элемент без типа point/edge/circle/plane")
    out: dict[str, Any] = {"type": e["type"], "kind": _text(e.get("kind", ""), "picked.kind", 40),
                           "part": _text(e.get("part", ""), "picked.part", 200)}
    fields = {"point": ("point",), "edge": ("a", "b"), "circle": ("centre", "normal"),
              "plane": ("origin", "normal", "point")}[e["type"]]
    for f in fields:
        out[f] = _vec(e.get(f), f"picked.{f}")
    if e["type"] == "circle":
        out["radius"] = round(_num(e.get("radius"), "picked.radius"), 4)
        out["closed"] = bool(e.get("closed", True))
    return out


def sanitize(state: Any) -> dict:
    """Проверить присланное и оставить только известное — чужого не хранить."""
    if not isinstance(state, dict):
        raise WindowStateError("состояние окна — объект")
    if len(json.dumps(state, ensure_ascii=False)) > MAX_BYTES:
        raise WindowStateError(f"состояние окна больше {MAX_BYTES // 1024} КБ")
    out: dict[str, Any] = {}
    sel = state.get("selected")
    out["selected"] = None if sel is None else _name(sel, "selected")
    out["hidden"] = _names(state.get("hidden", []), "hidden")
    out["ghosts"] = _names(state.get("ghosts", []), "ghosts")
    # Какие детали окно загрузило: по ним `window_show` проверяет имена, не
    # открывая проекта (открытие исполняет скрипты, а показ идёт в сервере).
    # Нет поля — окно старое, и имена не проверяются.
    if "parts" in state:
        out["parts"] = _names(state.get("parts", []), "parts")
    tool = state.get("tool", "none")
    if tool not in TOOLS:
        raise WindowStateError(f"tool: неизвестный инструмент {tool!r}")
    out["tool"] = tool
    section = state.get("section")
    if section is None:
        out["section"] = None
    else:
        if not isinstance(section, dict) or section.get("plane") not in PLANES:
            raise WindowStateError("section: ожидалось {plane: xy|xz|yz, at: число}")
        out["section"] = {"plane": section["plane"], "at": round(_num(section.get("at"), "section.at"), 4)}
    out["spread"] = round(min(1.0, max(0.0, _num(state.get("spread", 0), "spread"))), 4)
    # Ход шарниров, показанный в окне: имя звена → положение (° или мм).
    # Нули не храним — «в собранном положении» и есть отсутствие записи.
    joints = state.get("joints", {}) or {}
    if not isinstance(joints, dict) or len(joints) > 1000:
        raise WindowStateError("joints: ожидался объект имя звена → положение")
    out["joints"] = {_name(k, "joints"): round(_num(v, f"joints.{k}"), 4)
                     for k, v in joints.items() if v}
    camera = state.get("camera")
    if camera is None:
        out["camera"] = None
    else:
        if not isinstance(camera, dict):
            raise WindowStateError("camera: ожидался объект")
        cam = {k: _vec(camera.get(k), f"camera.{k}") for k in ("position", "target", "up")}
        if math.dist(cam["position"], cam["target"]) < 1e-6:
            raise WindowStateError("camera: камера стоит в точке, куда смотрит")
        cam["fov"] = round(_num(camera.get("fov", 45), "camera.fov"), 3)
        out["camera"] = cam
    readout = state.get("readout")
    if readout is None:
        out["readout"] = None
    else:
        if not isinstance(readout, dict) or readout.get("tool") not in ("distance", "probe"):
            raise WindowStateError("readout: ожидался {tool: distance|probe, text, detail}")
        out["readout"] = {"tool": readout["tool"], "text": _text(readout.get("text", ""), "readout.text", 200),
                          "detail": _text(readout.get("detail", ""), "readout.detail")}
    picked = state.get("picked", [])
    if not isinstance(picked, list) or len(picked) > 2:
        raise WindowStateError("picked: не больше двух элементов")
    out["picked"] = [_element(e) for e in picked]
    return out


def save(root: str | Path, state: dict, *, pid: int | None = None) -> dict:
    """Записать проверенное состояние с отметкой времени и процесса сервера."""
    record = {**state, "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
              "pid": os.getpid() if pid is None else pid}
    path = Path(root) / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    from datum.doc.params_edit import write_atomically

    write_atomically(path, json.dumps(record, ensure_ascii=False, indent=1))
    return record


def _alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:                             # pragma: no cover — чужой процесс
        return True
    return True


def load(root: str | Path) -> dict | None:
    """Последнее, что сообщило окно, с возрастом и тем, живо ли оно; None — не сообщало."""
    path = Path(root) / FILE
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        state = sanitize({k: v for k, v in record.items() if k not in ("at", "pid")})
        at = dt.datetime.fromisoformat(record["at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    age = (dt.datetime.now().astimezone() - at).total_seconds()
    return {**state, "at": record["at"], "age_s": round(max(0.0, age), 1),
            "open": _alive(record.get("pid"))}


def _fmt(v: list[float]) -> str:
    return ", ".join(f"{x:g}" for x in v)


def _looking(camera: dict) -> str:
    """Откуда смотрит камера — словами, как на кубе видов."""
    d = [p - t for p, t in zip(camera["position"], camera["target"])]
    n = math.sqrt(sum(x * x for x in d))
    d = [x / n for x in d]
    names = (("справа", "слева"), ("сзади", "спереди"), ("сверху", "снизу"))
    parts = [names[i][0 if d[i] > 0 else 1] for i in range(3) if abs(d[i]) > 0.35]
    return (" ".join(parts) or "сбоку") + f" (направление на камеру {_fmt([round(x, 2) for x in d])})"


def describe(state: dict | None) -> str:
    """Для агента — словами, по строке на пункт."""
    if state is None:
        return "окно ещё не сообщало, что показывает (не открывалось с этим проектом)"
    lines = [("окно открыто" if state.get("open") else "окно ЗАКРЫТО — ниже последнее, что оно показывало")
             + f", сведения от {state['at']} ({state['age_s']:g} с назад)"]
    lines.append(f"выделена: {state['selected']}" if state.get("selected") else "ничего не выделено")
    if state.get("hidden"):
        lines.append(f"скрыты: {', '.join(state['hidden'])}")
    if state.get("ghosts"):
        lines.append(f"образцы импорта на столе: {', '.join(state['ghosts'])}")
    tool = {"none": "обзор", "distance": "штангенциркуль", "probe": "толщина стенки",
            "move": "перемещение", "rotate": "поворот"}[state.get("tool", "none")]
    lines.append(f"инструмент: {tool}")
    if state.get("section"):
        s = state["section"]
        lines.append(f"срез: плоскость {s['plane'].upper()} на {s['at']:g} мм")
    if state.get("spread"):
        lines.append(f"вид разнесён на {state['spread'] * 100:.0f} % — детали на экране не на своих местах")
    for name, u in sorted((state.get("joints") or {}).items()):
        lines.append(f"шарнир {name}: показан в положении {u:g} — звено на экране не в "
                     f"собранном положении")
    if state.get("camera"):
        lines.append(f"камера смотрит {_looking(state['camera'])}, на точку {_fmt(state['camera']['target'])}")
    for i, e in enumerate(state.get("picked") or [], 1):
        where = {"point": lambda: _fmt(e["point"]),
                 "edge": lambda: f"{_fmt(e['a'])} — {_fmt(e['b'])}",
                 "circle": lambda: f"центр {_fmt(e['centre'])}, R {e['radius']:g}",
                 "plane": lambda: f"через {_fmt(e['origin'])}, нормаль {_fmt(e['normal'])}"}[e["type"]]()
        lines.append(f"выбрано {i}: {e['kind']} ({e['part']}) — {where}")
    if state.get("readout"):
        r = state["readout"]
        head = "замер штангенциркулем" if r["tool"] == "distance" else "толщина стенки"
        lines.append(f"{head}: {r['text']}" + ("\n  " + r["detail"].replace("\n", "\n  ") if r["detail"] else ""))
    return "\n".join(lines)
