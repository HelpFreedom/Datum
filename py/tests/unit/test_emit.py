"""События по ходу операции: деталь показывается, не дожидаясь остальных.

При открытии проекта семнадцать деталей из кеша ждали восемнадцатую, и окно всё
это время показывало пустую сцену (аудит 2026-11-21, §4.6).
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

import datum.tools.ops  # noqa: F401 — объявление операций
from datum.build import worker
from datum.server.app import create_app
from datum.server.events import EventHub
from datum.server.jobs import Job, JobManager, _EmitTail
from datum.server.security import CLIENT_HEADER, TOKEN_HEADER, ServerSecurity

TOKEN = "tok-emit-1"
WS = "ws://127.0.0.1:7777/ws"
AUTH = {TOKEN_HEADER: TOKEN, CLIENT_HEADER: "1"}


@pytest.fixture(autouse=True)
def no_emit_file():
    worker.emit_to(None)
    yield
    worker.emit_to(None)


# ------------------------------------------------------------------- воркер
def test_outside_a_job_emit_does_nothing():
    assert worker.emit({"t": "geom.updated", "part": "x"}) is False


def test_emit_appends_whole_lines_and_drops_what_is_too_long(tmp_path):
    path = tmp_path / "e.jsonl"
    worker.emit_to(str(path))
    assert worker.emit({"t": "build.progress", "part": "крышка", "done": 1, "total": 2})
    assert worker.emit({"t": "build.progress", "part": "корпус", "done": 2, "total": 2})
    assert worker.emit({"t": "log", "text": "я" * 5000}) is False, "длинное придёт с результатом"
    lines = path.read_text("utf-8").splitlines()
    assert [json.loads(x)["part"] for x in lines] == ["крышка", "корпус"]


def test_the_tail_never_reads_half_a_line(tmp_path):
    path = tmp_path / "e.jsonl"
    path.write_bytes(b'{"t": "log", "n": 1}\n{"t": "lo')
    tail = _EmitTail(str(path))
    assert [e["n"] for e in tail.read()] == [1]
    assert tail.read() == []                              # хвост без перевода — не строка
    with open(path, "ab") as f:
        f.write(b'g", "n": 2}\nnot json\n[1, 2]\n{"t": "log", "n": 3}\n')
    assert [e["n"] for e in tail.read()] == [2, 3], "мусор и не-словари — мимо"
    assert tail.read() == []
    assert _EmitTail(str(tmp_path / "нет")).read() == []


# ------------------------------------------------------------------- сервер
def test_emitted_events_are_filtered_like_the_result_ones_and_not_repeated(tmp_path):
    """Событие по ходу работы пишет тот же код проекта, что и результат: вид
    сверяется с тем же списком. А то, что уже разослано, с результатом второй
    раз не идёт — иначе окно грузило бы каждую деталь дважды."""
    path = tmp_path / "e.jsonl"
    geom = {"t": "geom.updated", "part": "корпус", "digest": "a" * 64}
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in [
        geom, {"t": "job.done", "job": "чужое"}, {"t": "hello"},
        {"t": "build.progress", "part": "корпус", "done": 1, "total": 2}]) + "\n", "utf-8")
    hub = EventHub()
    manager = JobManager(hub)
    job = Job(id="j1", op="part_build", kwargs={}, status="running")
    manager._publish_emitted(job, _EmitTail(str(path)))
    assert [e["t"] for e in hub.snapshot()] == ["geom.updated", "build.progress"]
    assert hub.snapshot()[0]["job"] == "j1"
    job.result = {"events": [geom, {"t": "geom.updated", "part": "крышка", "digest": "b" * 64}]}
    manager._republish(job)
    assert [e.get("part") for e in hub.snapshot() if e["t"] == "geom.updated"] == \
        ["корпус", "крышка"]


FAST_AND_SLOW = '''
import time

from datum.build import P, part
from datum.geom import box


@part("а_быстрая")
def fast():
    return box(0, P.A, 0, P.A, 0, P.A)


@part("б_медленная")
def slow():
    time.sleep(3.0)
    return box(0, P.A, 0, P.A, 0, 2 * P.A)
'''


def test_a_ready_part_reaches_the_window_before_the_slow_one_is_done(tmp_path):
    root = tmp_path / "проект"
    (root / "parts").mkdir(parents=True)
    (root / "params").mkdir()
    (root / "params" / "base.yaml").write_text(
        'A: {value: 10.0, unit: mm, provenance: chosen, reason: "—"}\n', encoding="utf-8")
    (root / "parts" / "p.py").write_text(FAST_AND_SLOW, encoding="utf-8")
    app = create_app(root, security=ServerSecurity(token=TOKEN))
    with TestClient(app, base_url="http://127.0.0.1:7777") as client, \
            client.websocket_connect(WS) as ws:
        ws.send_json({"token": TOKEN})
        assert ws.receive_json()["t"] == "hello"
        job = client.post("/api/jobs", headers=AUTH,
                          json={"op": "part_build", "args": {"project": str(root)}}).json()["job"]
        seen: list[tuple[float, dict]] = []
        for _ in range(20):
            event = ws.receive_json()
            seen.append((time.monotonic(), event))
            if event["t"] in ("job.done", "job.failed", "job.cancelled"):
                break
        assert seen[-1][1]["t"] == "job.done", seen[-1][1]
        geoms = [(t, e) for t, e in seen if e["t"] == "geom.updated"]
        assert [e["part"] for _, e in geoms] == ["а_быстрая", "б_медленная"], \
            "каждая деталь — ровно один раз"
        assert geoms[1][0] - geoms[0][0] > 2.0, \
            "быстрая деталь показана, пока медленная ещё собирается"
        progress = [e for _, e in seen if e["t"] == "build.progress"]
        assert [(e["part"], e["done"], e["total"]) for e in progress] == \
            [("а_быстрая", 1, 2), ("б_медленная", 2, 2)]
        assert all(e["job"] == job for _, e in geoms)
        # Событие по ходу работы — с рабочим адресом: объект уже лежит.
        got = client.get(geoms[0][1]["url"], headers={TOKEN_HEADER: TOKEN})
        assert got.status_code == 200 and got.content[:4] == b"glTF"
        # В ответе задания служебного списка нет, как и прежде.
        result = client.get(f"/api/jobs/{job}", headers={TOKEN_HEADER: TOKEN}).json()["result"]
        assert "events" not in result and set(result["parts"]) == {"а_быстрая", "б_медленная"}
