"""Что видит человек в окне — и вид стола для агента.

Агент в терминале видит проект, но не окно. Окно сообщает серверу, что
показывает (`window_report`), агент читает (`window_state`) и смотрит на стол
картинкой (`scene_view`). Писать «что видит человек» может только окно.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from datum.doc import window as W
from datum.server.app import create_app
from datum.server.security import CLIENT_HEADER, TOKEN_HEADER, ServerSecurity
from datum.tools.ops import (clear_project_cache, scene_view, window_report,
                             window_state)

TOKEN = "tok-window"

STATE = {
    "selected": "кубик",
    "hidden": [],
    "ghosts": [],
    "tool": "distance",
    "section": {"plane": "xy", "at": 5},
    "spread": 0,
    "camera": {"position": [300, -200, 250], "target": [128, 128, 10], "up": [0, 0, 1], "fov": 45},
    "readout": {"tool": "distance", "text": "21,213 мм", "detail": "вершина — центр окружности"},
    "picked": [
        {"type": "circle", "kind": "окружность", "part": "кубик", "centre": [128, 128, 20],
         "normal": [0, 0, 1], "radius": 4, "closed": True},
        {"type": "point", "kind": "вершина", "part": "кубик", "point": [138, 118, 20]},
    ],
}


def run(op, **kwargs):
    clear_project_cache()
    return asyncio.run(op(**kwargs))


# ---------------------------------------------------------------- проверка
@pytest.mark.parametrize("bad, hint", [
    ([], "объект"),
    ({**STATE, "tool": "молоток"}, "инструмент"),
    ({**STATE, "camera": {**STATE["camera"], "position": [1, 2, float("nan")]}}, "конечное"),
    ({**STATE, "camera": {**STATE["camera"], "position": [128, 128, 10]}}, "стоит в точке"),
    ({**STATE, "section": {"plane": "xw", "at": 1}}, "section"),
    ({**STATE, "picked": [{"type": "шар"}]}, "picked"),
    ({**STATE, "hidden": ["я" * 70000]}, "КБ"),
])
def test_junk_from_the_window_is_refused(bad, hint):
    with pytest.raises(W.WindowStateError, match=hint):
        W.sanitize(bad)


def test_only_known_fields_are_kept():
    clean = W.sanitize({**STATE, "пароль": "секрет", "picked": [{**STATE["picked"][1], "лишнее": 1}]})
    assert "пароль" not in clean and "лишнее" not in clean["picked"][0]
    assert clean["camera"]["target"] == [128.0, 128.0, 10.0]


def test_open_while_the_server_lives_closed_after(cube):
    W.save(cube, W.sanitize(STATE))                      # pid — этого процесса
    seen = W.load(cube)
    assert seen["open"] and seen["selected"] == "кубик" and seen["age_s"] < 5
    gone = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True, check=True)
    W.save(cube, W.sanitize(STATE), pid=int(gone.stdout))
    assert W.load(cube)["open"] is False
    assert "окно ЗАКРЫТО" in W.describe(W.load(cube))


def test_the_agent_reads_it_in_words(cube):
    assert "не сообщало" in run(window_state, project=str(cube))["report"]
    assert not run(window_report, project=str(cube), state=STATE).get("error")
    text = run(window_state, project=str(cube))["report"]
    for piece in ("выделена: кубик", "инструмент: штангенциркуль", "срез: плоскость XY на 5 мм",
                  "выбрано 1: окружность (кубик) — центр 128, 128, 20, R 4",
                  "выбрано 2: вершина (кубик) — 138, 118, 20",
                  "замер штангенциркулем: 21,213 мм", "камера смотрит справа спереди сверху"):
        assert piece in text, piece


def test_the_window_can_report_and_the_agent_cannot(latch):
    """Иначе «что видит человек» можно было бы подделать из терминала."""
    app = create_app(latch, security=ServerSecurity(token=TOKEN))
    with TestClient(app, base_url="http://127.0.0.1:7777") as c:
        head = {TOKEN_HEADER: TOKEN, CLIENT_HEADER: "1"}
        body = {"project": str(latch), "state": STATE}
        agent = c.post("/api/ops/window_report", json=body, headers={**head, "X-Datum-Actor": "agent"})
        assert agent.status_code == 403
        assert not (latch / W.FILE).exists()
        user = c.post("/api/ops/window_report", json=body, headers={**head, "X-Datum-Actor": "user"})
        assert user.status_code == 200, user.text
        assert user.json()["result"] == {"ok": True}
        # Прочесть агенту можно.
        read = c.post("/api/ops/window_state", json={"project": str(latch)},
                      headers={**head, "X-Datum-Actor": "agent"})
        assert "выделена: кубик" in read.json()["result"]["report"]


# ------------------------------------------------------------------ вид стола
def _image(cube, out):
    return Image.open(cube / out["image"]["path"])


def test_the_scene_shows_every_part_where_it_stands(cube):
    (cube / "placement.yaml").write_text("кубик: {move: [40, 0, 0]}\n", encoding="utf-8")
    out = run(scene_view, project=str(cube))
    assert not out.get("error"), out.get("error")
    assert out["parts"] == ["кубик"]
    # Положение из placement.yaml — в картинке и в числах.
    assert "кубик: X 158.0…178.0" in out["report"]
    assert _image(cube, out).size[0] > 800


def test_the_scene_follows_the_window(cube):
    W.save(cube, W.sanitize({**STATE, "hidden": ["кубик"]}))
    assert "скрыты в окне" in run(scene_view, project=str(cube))["error"]
    W.save(cube, W.sanitize(STATE))
    out = run(scene_view, project=str(cube))
    assert "оттуда, откуда смотрит окно" in out["report"]
    assert "оранжевым — выбранное в окне" in out["report"]      # замер человека — на картинке
    no_ghosts = run(scene_view, project=str(cube), ghosts=False)
    W.save(cube, W.sanitize(STATE), pid=999_999_999)       # окно закрыто — его вида нет
    closed = run(scene_view, project=str(cube))
    assert _image(cube, no_ghosts).size == _image(cube, out).size
    assert _image(cube, closed).size[1] < _image(cube, out).size[1]


def test_unused_imported_models_stand_on_the_table_as_ghosts(cube):
    import trimesh

    from datum.tools.ops import import_model

    src = cube.parent / "модель.stl"
    trimesh.creation.box(extents=(10, 10, 10)).export(src)
    assert not run(import_model, project=str(cube), path=str(src), name="модель").get("error")
    out = run(scene_view, project=str(cube))
    assert out["ghosts"] == ["модель"] and "образец модель" in out["report"]
    assert run(scene_view, project=str(cube), ghosts=False)["ghosts"] == []


def test_a_joint_shown_open_is_told_to_the_agent(cube):
    """Окно показало крышку открытой — агент знает, что на экране звено не на
    месте; нулевые положения не хранятся, мусор отвергается."""
    clean = W.sanitize({**STATE, "joints": {"крышка": 37.5, "ползун": 0}})
    assert clean["joints"] == {"крышка": 37.5}
    with pytest.raises(W.WindowStateError, match="joints"):
        W.sanitize({**STATE, "joints": {"крышка": "открыта"}})
    with pytest.raises(W.WindowStateError, match="joints"):
        W.sanitize({**STATE, "joints": [37]})
    W.save(cube, clean)
    words = W.describe(W.load(cube))
    assert "шарнир крышка: показан в положении 37.5" in words
    assert "joints" not in W.sanitize(STATE) or W.sanitize(STATE)["joints"] == {}


# ------------------------------------------------------------------ показ агента
def _show(**kwargs):
    from datum.tools.ops import window_show

    return run(window_show, **kwargs)


def _serving():
    """Как внутри сервера окна (`server/jobs.py` ставит это на время вызова)."""
    return W.SERVING.set(True)


def test_show_refuses_when_no_window_is_open(cube):
    """Показать некому — так и сказать, а не «показано» в пустоту."""
    assert "не открыто" in _show(project=str(cube), parts=["кубик"])["error"]
    token = _serving()
    try:                      # сервер есть, но окно ни разу не сообщило о себе
        assert "не открыто" in _show(project=str(cube), parts=["кубик"])["error"]
    finally:
        W.SERVING.reset(token)


def test_show_sends_the_view_to_the_window(cube):
    W.save(cube, W.sanitize({**STATE, "parts": ["кубик"]}))                      # окно открыто (pid — живой)
    token = _serving()
    try:
        out = _show(project=str(cube), parts=["кубик"], isolate=True, plane="xz", at=128.0,
                    note="вот стенка")
    finally:
        W.SERVING.reset(token)
    assert not out.get("error"), out.get("error")
    (event,) = out["events"]
    assert event["t"] == "window.show" and event["parts"] == ["кубик"] and event["isolate"]
    assert event["section"] == {"plane": "xz", "at": 128.0} and event["note"] == "вот стенка"
    assert abs(event["at"] - __import__("time").time()) < 60      # окно отсеет повтор
    assert "Вернуть как было" in out["report"]
    # Проект не меняется: показ — вид.
    assert not (cube / "placement.yaml").exists()


@pytest.mark.parametrize("args, hint", [
    (dict(parts=["кубек"]), "кубик"),                         # опечатка — подсказка
    (dict(joint="кубик"), "парой"),
    (dict(isolate=True), "parts не названы"),
    (dict(plane="xy"), "парой"),
    (dict(at=5.0), "парой"),
    (dict(joint="кубик", joint_u=10.0), "шарнира"),
    (dict(note="я" * 301), "подпись"),
    (dict(), "показывать нечего"),
])
def test_show_refuses_what_it_cannot_show(cube, args, hint):
    W.save(cube, W.sanitize({**STATE, "parts": ["кубик"]}))
    token = _serving()
    try:
        out = _show(project=str(cube), **args)
    finally:
        W.SERVING.reset(token)
    assert hint in out["error"], out["error"]


def test_show_checks_the_joint_and_the_window_rule(latch):
    """Ход — в пределах ползунка окна; срез при сдвинутом звене окно гасит,
    поэтому такой показ — отказ, а не половина показа."""
    (latch / "assembly.yaml").write_text(
        (latch / "assembly.yaml").read_text(encoding="utf-8")
        + 'joints:\n  крышка: {kind: prismatic, axis: "+Z", range: [0, 15]}\n', encoding="utf-8")
    W.save(latch, W.sanitize({**STATE, "selected": None, "picked": [],
                                 "parts": ["корпус", "крышка"]}))
    token = _serving()
    try:
        assert "вне хода" in _show(project=str(latch), joint="крышка", joint_u=20.0)["error"]
        assert "joint_u" in _show(project=str(latch), joint="крышка")["error"]
        both = _show(project=str(latch), joint="крышка", joint_u=5.0, plane="xy", at=10.0)
        assert "по очереди" in both["error"]
        ok = _show(project=str(latch), joint="крышка", joint_u=5.0)
        assert ok["events"][0]["joint"] == {"part": "крышка", "u": 5.0}
    finally:
        W.SERVING.reset(token)


def test_the_server_delivers_the_show_to_the_window(cube):
    """Через сервер: агентский вызов ставит событие `window.show` в ленту окна."""
    app = create_app(cube, security=ServerSecurity(token=TOKEN))
    with TestClient(app, base_url="http://127.0.0.1:7777") as c:
        head = {TOKEN_HEADER: TOKEN, CLIENT_HEADER: "1", "X-Datum-Actor": "agent"}
        W.save(cube, W.sanitize({**STATE, "parts": ["кубик"]}))
        r = c.post("/api/ops/window_show", json={"project": str(cube), "parts": ["кубик"],
                                                 "note": "смотрите сюда"}, headers=head)
        assert r.status_code == 200, r.text
        result = r.json()["result"]
        assert not result.get("error"), result.get("error")
        assert "events" not in result                  # внутренняя передача снята
        shows = [e for e in app.state.datum.hub.snapshot() if e["t"] == "window.show"]
        assert len(shows) == 1 and shows[0]["parts"] == ["кубик"]
        assert shows[0]["actor"] == "agent" and shows[0]["note"] == "смотрите сюда"


def test_the_window_reports_which_parts_it_has():
    """По ним показ проверяет имена, не открывая проекта (открытие исполняет
    скрипты, а показ идёт в процессе сервера). Старое окно без поля — без проверки."""
    assert W.sanitize({**STATE, "parts": ["кубик", "крышка"]})["parts"] == ["кубик", "крышка"]
    assert "parts" not in W.sanitize(STATE)
    with pytest.raises(W.WindowStateError, match="parts"):
        W.sanitize({**STATE, "parts": "кубик"})
