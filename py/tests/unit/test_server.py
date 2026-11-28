"""Локальный сервер: охрана, операции, задания, объекты, события."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import datum.tools.ops  # noqa: F401 — объявление операций
from datum.server.app import create_app
from datum.server.handshake import READY_MARKER, read_handshake, write_handshake
from datum.server.run import serve
from datum.server.security import CLIENT_HEADER, TOKEN_HEADER, ServerSecurity

TOKEN = "tok-test-123"


@pytest.fixture
def app(latch):
    return create_app(latch, security=ServerSecurity(token=TOKEN))


@pytest.fixture
def client(app):
    # base_url важен: по умолчанию TestClient шлёт «Host: testserver», а это не
    # локальное имя — проверка от перепривязки DNS честно отвечает 403.
    with TestClient(app, base_url="http://127.0.0.1:7777") as c:
        yield c


#: Адрес событий полностью: `websocket_connect` у TestClient не берёт
#: `base_url` и шлёт «Host: testserver» — а Host у WebSocket проверяется.
WS = "ws://127.0.0.1:7777/ws"


def auth(mutating: bool = False) -> dict:
    h = {TOKEN_HEADER: TOKEN}
    if mutating:
        h[CLIENT_HEADER] = "1"
    return h


# ------------------------------------------------------------------ охрана
def test_healthz_is_open(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_healthz_tells_the_project_only_to_the_token_holder(client):
    """Живость открыта, а путь проекта — нет: незнакомцу на той же машине его
    знать незачем. Свой клиент (MCP) шлёт токен и сверяет проект."""
    assert client.get("/healthz").json() == {"ok": True}
    told = client.get("/healthz", headers=auth()).json()
    assert told["ok"] is True and told["project"] and "seq" in told
    assert client.get("/healthz", headers={TOKEN_HEADER: "wrong-token"}).json() == {"ok": True}


def test_host_is_checked_before_the_open_path(app):
    """Страница с перепривязкой DNS не должна получить даже проверку живости."""
    with TestClient(app, base_url="http://evil.example.com") as c:
        assert c.get("/healthz").status_code == 403


def test_everything_else_needs_a_token(client):
    for path in ("/api/ops", "/api/jobs", "/obj/" + "a" * 64 + ".glb"):
        assert client.get(path).status_code == 403, path


def test_wrong_token_is_refused(client):
    # Значение заголовка — ASCII: HTTP не допускает иного, и клиент с кириллицей
    # просто не отправит запрос. Защита token_ok от не-ASCII проверяется
    # отдельно, на уровне самой функции: сырой сокет прислать такое может.
    r = client.get("/api/ops", headers={TOKEN_HEADER: "wrong-token"})
    assert r.status_code == 403 and TOKEN_HEADER in r.json()["error"]


def test_non_local_host_is_refused(client):
    r = client.get("/api/ops", headers={**auth(), "Host": "evil.example.com"})
    assert r.status_code == 403 and "перепривязк" in r.json()["error"]


def test_mutating_request_needs_the_client_header(client):
    r = client.post("/api/ops/datum_status", headers=auth(), json={})
    assert r.status_code == 403 and CLIENT_HEADER in r.json()["error"]
    r = client.post("/api/ops/datum_status", headers=auth(True), json={})
    assert r.status_code == 200


def test_no_cors_headers_are_ever_sent(client):
    """Их отсутствие и есть защита. Появление — снятие защиты разом."""
    for r in (client.get("/healthz"), client.get("/api/ops", headers=auth())):
        assert not [h for h in r.headers if h.lower().startswith("access-control-")]
    assert client.get("/healthz").headers["x-content-type-options"] == "nosniff"


# ------------------------------------------------------------------ операции
def test_ops_are_listed_with_their_danger(client):
    data = client.get("/api/ops", headers=auth()).json()
    names = {o["name"] for o in data["ops"]}
    assert {"check_run", "part_build", "snapshot_create"} <= names
    snap = next(o for o in data["ops"] if o["name"] == "snapshot_create")
    assert snap["danger"] == "mutates" and snap["isolate"] is True
    assert any(a["name"] == "project" and a["required"] for a in snap["args"])


def test_running_an_operation(client, latch):
    r = client.post("/api/ops/check_run", headers=auth(True),
                    json={"project": str(latch)})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "done"
    assert body["result"]["ok"] is True
    assert "ИТОГ: OK" in body["result"]["report"]


def test_unknown_operation(client):
    r = client.post("/api/ops/нет-такой", headers=auth(True), json={})
    assert r.status_code == 404


def test_bad_body(client):
    r = client.post("/api/ops/datum_status", headers=auth(True),
                    content="не json".encode("utf-8"))
    assert r.status_code == 400


# ------------------------------------------------------------------ задания
def test_job_lifecycle(client, latch):
    r = client.post("/api/jobs", headers=auth(True),
                    json={"op": "part_build", "args": {"project": str(latch)}})
    job_id = r.json()["job"]

    for _ in range(300):
        state = client.get(f"/api/jobs/{job_id}", headers=auth()).json()
        if state["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(0.05)
    assert state["status"] == "done", state
    assert state["result"]["ok"] is True
    assert any(j["id"] == job_id for j in client.get("/api/jobs",
                                                     headers=auth()).json()["jobs"])


def test_unknown_job(client):
    assert client.get("/api/jobs/нетути", headers=auth()).status_code == 404
    assert client.post("/api/jobs/нетути/cancel",
                       headers=auth(True)).status_code == 404


SLOW_PART = '''
import time

from datum.build import P, part
from datum.geom import box


@part("медленная")
def build():
    time.sleep(30)
    return box(0, P.A, 0, P.A, 0, P.A)
'''


@pytest.fixture
def slow_project(tmp_path):
    root = tmp_path / "медленный"
    (root / "parts").mkdir(parents=True)
    (root / "params").mkdir(parents=True)
    (root / "params" / "base.yaml").write_text(
        'A: {value: 10.0, unit: mm, provenance: chosen, reason: "—"}\n',
        encoding="utf-8")
    (root / "parts" / "p.py").write_text(SLOW_PART, encoding="utf-8")
    return root


def test_cancelling_a_long_job(slow_project):
    """Отмена настоящая: процесс убивается, а не уговаривается."""
    # Сервер открыт на ОДНОМ проекте и чужие в теле запроса отвергает, поэтому
    # медленному проекту — свой сервер.
    #
    # `with` здесь обязателен, и вот почему (на macOS это был настоящий
    # ВИС, а не падение). Без него TestClient заводит СВОЮ петлю событий на
    # каждый запрос и гасит её после ответа (`_portal_factory` в
    # starlette.testclient), а гашение вызывает
    # `loop.shutdown_default_executor()` — тот самый join потоков пула. Сборка
    # же ждёт процесса в `worker.wait()`, запущенном в этом пуле
    # (`loop.run_in_executor` в jobs.py). Итог: ЛЮБОЙ следующий запрос вставал
    # ровно на время сборки — при `sleep(30)` в детали это 30 с, а будь сборка
    # длиннее, ожидание не кончилось бы никогда. `with` держит одну петлю (и
    # один пул) на весь тест, поэтому отмена доходит до воркера сразу.
    with TestClient(create_app(slow_project, security=ServerSecurity(token=TOKEN)),
                    base_url="http://127.0.0.1") as client:
        r = client.post("/api/jobs", headers=auth(True),
                        json={"op": "part_build",
                              "args": {"project": str(slow_project)}})
        job_id = r.json()["job"]

        for _ in range(100):
            if client.get(f"/api/jobs/{job_id}",
                          headers=auth()).json()["status"] == "running":
                break
            time.sleep(0.05)

        t0 = time.perf_counter()
        res = client.post(f"/api/jobs/{job_id}/cancel", headers=auth(True)).json()
        assert res["cancelled"] is True

        for _ in range(200):
            state = client.get(f"/api/jobs/{job_id}", headers=auth()).json()
            if state["status"] in ("cancelled", "failed", "done"):
                break
            time.sleep(0.05)
        assert state["status"] == "cancelled", state
        assert time.perf_counter() - t0 < 15.0, "отмена не должна ждать долго"


# ------------------------------------------------------------------ объекты
def test_object_name_must_be_a_digest(client):
    for bad in ("файл.glb", "../../etc/passwd", "abc.glb", "x" * 64 + ".exe",
                "a" * 63 + ".glb"):
        r = client.get(f"/obj/{bad}", headers=auth())
        assert r.status_code in (400, 404), bad


def test_path_traversal_is_impossible(client):
    r = client.get("/obj/%2e%2e%2f%2e%2e%2fetc%2fpasswd", headers=auth())
    assert r.status_code in (400, 404)


def test_object_is_served_with_immutable_cache(client, latch):
    client.post("/api/ops/part_build", headers=auth(True), json={"project": str(latch)})
    objects = Path(latch) / ".datum" / "cache" / "objects"
    files = list(objects.rglob("*.mesh"))
    assert files, "сборка должна была положить объекты в кеш"
    name = files[0].name
    r = client.get(f"/obj/{name}", headers=auth())
    assert r.status_code == 200 and r.content == files[0].read_bytes()
    assert "immutable" in r.headers["cache-control"]


def test_missing_object(client):
    r = client.get(f"/obj/{'0' * 64}.glb", headers=auth())
    assert r.status_code == 404


# ------------------------------------------------------------------ события
def test_websocket_needs_the_token(client):
    with client.websocket_connect(WS) as ws:
        ws.send_json({"token": "не тот"})
        with pytest.raises(Exception):
            ws.receive_json()


def test_websocket_checks_the_host_itself(app):
    """Средний слой охраны — HTTP, до WebSocket он не доходит. Перепривязка DNS
    с чужого имени отказывается и здесь — до приёма соединения и до токена."""
    from starlette.websockets import WebSocketDisconnect

    with TestClient(app, base_url="http://evil.example.com") as c:
        with pytest.raises(WebSocketDisconnect) as e:
            with c.websocket_connect("ws://evil.example.com/ws",
                                     headers={"origin": "http://127.0.0.1"}) as ws:
                ws.send_json({"token": TOKEN, "since": 0})
                ws.receive_json()
        assert e.value.code == 4403


def test_websocket_hello_and_job_events(client, latch):
    with client.websocket_connect(WS) as ws:
        ws.send_json({"token": TOKEN, "since": 0})
        hello = ws.receive_json()
        assert hello["t"] == "hello" and hello["project"].endswith("latch")

        client.post("/api/jobs", headers=auth(True),
                    json={"op": "datum_status", "args": {}})
        kinds = []
        for _ in range(4):
            kinds.append(ws.receive_json()["t"])
            if "job.done" in kinds:
                break
        assert "job.submitted" in kinds and "job.done" in kinds


def test_replay_after_reconnect(client):
    """Разрыв связи не должен оставлять вьюпорт с устаревшим состоянием."""
    client.post("/api/jobs", headers=auth(True), json={"op": "datum_status", "args": {}})
    time.sleep(0.3)
    with client.websocket_connect(WS) as ws:
        ws.send_json({"token": TOKEN, "since": 0})
        hello = ws.receive_json()
        assert hello["complete"] is True and hello["resync"] is False
        seen = [ws.receive_json()["t"] for _ in range(2)]
        assert "job.submitted" in seen


def test_a_fresh_page_gets_no_replay(client):
    """Новая страница читает состояние целиком сама: досылка всего кольца
    заставляла её грузить устаревшую геометрию — сразу после перезагрузки
    из-за нехватки памяти (аудит 2026-11-21)."""
    client.post("/api/jobs", headers=auth(True), json={"op": "datum_status", "args": {}})
    time.sleep(0.3)
    with client.websocket_connect(WS) as ws:
        ws.send_json({"token": TOKEN, "since": 0, "fresh": True})
        hello = ws.receive_json()
        assert hello["t"] == "hello" and hello["complete"] is True and hello["seq"] > 0
        client.post("/api/jobs", headers=auth(True), json={"op": "datum_status", "args": {}})
        first = ws.receive_json()
        assert first["seq"] > hello["seq"], "пришло новое, а не прошлое"


def test_a_malformed_first_frame_closes_the_socket(client):
    from starlette.websockets import WebSocketDisconnect

    for frame in ([], "строка", {"token": TOKEN, "since": "не число"}):
        if isinstance(frame, dict):
            with client.websocket_connect(WS) as ws:
                ws.send_json(frame)
                assert ws.receive_json()["t"] == "hello"
            continue
        with pytest.raises(WebSocketDisconnect) as e:
            with client.websocket_connect(WS) as ws:
                ws.send_json(frame)
                ws.receive_json()
        assert e.value.code == 4401


def test_window_service_calls_are_marked_and_leave_first(client, latch):
    """Лента — про действия человека и агента; служебные вызовы окна (отчёт о
    виде, чтение состояния) помечены и вытесняются первыми."""
    user = {**auth(True), "X-Datum-Actor": "user"}
    client.post("/api/ops/datum_status", headers=user, json={})
    client.post("/api/ops/project_info", headers=user, json={"project": str(latch)})
    agent = {**auth(True), "X-Datum-Actor": "agent"}
    client.post("/api/ops/project_info", headers=agent, json={"project": str(latch)})
    jobs = client.get("/api/jobs?brief=true", headers=auth()).json()["jobs"]
    marks = [(j["op"], j["actor"], j["service"]) for j in jobs[-3:]]
    assert marks == [("datum_status", "user", False), ("project_info", "user", True),
                     ("project_info", "agent", False)]


# ------------------------------------------------------------------ рукопожатие
def test_handshake_file_is_private(tmp_path):
    path = write_handshake(port=1234, token="секрет", project="/тут",
                           path=tmp_path / "hs.json")
    mode = os.stat(path).st_mode & 0o777
    assert mode == 0o600, f"права {oct(mode)} — токен должен быть виден только хозяину"

    hs = read_handshake(path)
    assert hs.port == 1234 and hs.token == "секрет"
    assert not path.exists(), "файл читается один раз и удаляется"


def test_ready_marker_is_stable():
    assert READY_MARKER == "DATUM_READY"


# ------------------------------------------------------- привязка к петле
def test_serve_refuses_to_leave_the_loopback(latch, capsys):
    """Сервер исполняет произвольные скрипты сборки и раздаёт файлы проекта.
    Вынести его в сеть — раздать это всем, кто дотянется до машины. Поэтому
    непетлевая привязка это отказ на входе, а не привязка, чей каждый запрос
    потом отвергнет охрана."""
    for host in ("0.0.0.0", "192.168.1.10", "example.com"):
        assert serve(latch, host=host) == 2, host
        assert "только на петлевом" in capsys.readouterr().err


def test_serve_rejects_a_missing_project(tmp_path, capsys):
    assert serve(tmp_path / "нет-такого") == 2
    assert "нет каталога проекта" in capsys.readouterr().err


# ------------------------------------------------- события самой операции
def test_operation_events_reach_the_socket(client, latch):
    """Сборка обязана порождать geom.updated с адресом, по которому деталь
    действительно лежит. Это единственная дорога геометрии в окно."""
    with client.websocket_connect(WS) as ws:
        ws.send_json({"token": TOKEN})
        assert ws.receive_json()["t"] == "hello"

        r = client.post("/api/ops/part_build", headers=auth(True),
                        json={"project": str(latch)})
        assert r.status_code == 200, r.text

        seen = {}
        for _ in range(12):
            event = ws.receive_json()
            if event["t"] == "geom.updated":
                seen[event["part"]] = event
            if event["t"] == "job.done":
                break

    assert seen, "geom.updated не пришло"
    for part, event in seen.items():
        assert event["url"] == f"/obj/{event['digest']}.glb"
        assert event["tris"] > 0 and len(event["bbox"]) == 6
        # Адрес обязан работать: событие без доступного объекта бесполезно.
        got = client.get(event["url"], headers=auth())
        assert got.status_code == 200
        assert got.content[:4] == b"glTF"
        assert len(got.content) == event["bytes"]


def test_geometry_events_precede_the_finish(client, latch):
    """Окну полезнее получить геометрию до того, как ему скажут, что задание
    кончилось: иначе оно на мгновение показывает «готово» без детали."""
    with client.websocket_connect(WS) as ws:
        ws.send_json({"token": TOKEN})
        ws.receive_json()
        client.post("/api/ops/part_build", headers=auth(True),
                    json={"project": str(latch)})
        order = []
        for _ in range(12):
            order.append(ws.receive_json()["t"])
            if order[-1] == "job.done":
                break
    assert "geom.updated" in order
    assert order.index("geom.updated") < order.index("job.done")


def test_an_operation_cannot_forge_service_events():
    """Список `events` наполняет операция, а она исполняет код проекта. Если бы
    сервер рассылал что прислали, скрипт детали мог бы наврать окну про
    `hello` или `job.done` — например, изобразить успешную сборку."""
    from datum.server.events import EventHub
    from datum.server.jobs import PUBLISHABLE_EVENTS, Job, JobManager

    hub = EventHub()
    manager = JobManager(hub)
    job = Job(id="j1", op="part_build", kwargs={}, status="done", result={
        "events": [
            {"t": "geom.updated", "part": "корпус", "digest": "a" * 64},
            {"t": "job.done", "job": "чужое"},
            {"t": "hello", "project": "подделка"},
            {"t": "error", "message": "враньё"},
            "не словарь",
        ]})
    manager._republish(job)

    kinds = [e["t"] for e in hub.snapshot()]
    assert kinds == ["geom.updated"], kinds
    assert "job.done" not in PUBLISHABLE_EVENTS and "hello" not in PUBLISHABLE_EVENTS


def test_republished_events_carry_their_job():
    """Иначе в ленте не отличить, какая сборка произвела какую геометрию."""
    from datum.server.events import EventHub
    from datum.server.jobs import Job, JobManager

    hub = EventHub()
    JobManager(hub)._republish(Job(id="j7", op="part_build", kwargs={}, result={
        "events": [{"t": "geom.updated", "part": "крышка"}]}))
    assert hub.snapshot()[-1]["job"] == "j7"


# ------------------------------------------------------------------ окно
@pytest.fixture
def ui(tmp_path):
    """Подобие собранного бандла: страница и файл с отпечатком в имени."""
    root = tmp_path / "бандл"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(
        '<!doctype html><script type="module" src="/assets/main-abc123.js"></script>',
        encoding="utf-8")
    (root / "assets" / "main-abc123.js").write_text("console.log(1)", encoding="utf-8")
    return root


@pytest.fixture
def ui_client(latch, ui):
    app = create_app(latch, security=ServerSecurity(token=TOKEN), ui=ui)
    with TestClient(app, base_url="http://127.0.0.1:7777") as c:
        yield c


def test_window_bundle_is_served_without_a_token(ui_client):
    """К переходу браузера на страницу заголовок не приложишь, поэтому бандл
    открыт. Секретов в нём нет: токен окно получает из preload Electron, а не
    из страницы, — открытое в обычном браузере окно просто мертво."""
    for path in ("/", "/index.html", "/assets/main-abc123.js"):
        assert ui_client.get(path).status_code == 200, path


def test_opening_the_window_does_not_open_the_rest(ui_client):
    """Главное следствие, которое обязано остаться в силе."""
    assert ui_client.get("/api/ops").status_code == 403
    assert ui_client.get(f"/obj/{'a' * 64}.glb").status_code == 403


@pytest.mark.parametrize("path", [
    "/assets/..%2F..%2Fetc%2Fpasswd",
    "/assets/sub/dir.js",
    "/assets/.env",
    "/assets/",
    "/assets/%D0%BD%D0%B5%D1%82.js",          # не-ASCII имя
])
def test_asset_names_outside_the_rule_need_a_token(ui_client, path):
    """Имя файла бандла нарочно узкое: ни косых черт, ни точки в начале. Всё
    остальное — уже не бандл, и открытым быть не должно."""
    assert ui_client.get(path).status_code == 403, path


def test_a_missing_asset_is_a_plain_404(ui_client):
    assert ui_client.get("/assets/нету.js".replace("нету", "nope")).status_code == 404


def test_cache_headers_match_what_the_files_are(ui_client):
    """Страницу кешировать нельзя — в ней имена файлов бандла, и после
    пересборки окно обязано увидеть новые. Сами файлы, наоборот, навсегда:
    Vite вписывает отпечаток в имя."""
    assert ui_client.get("/").headers["cache-control"] == "no-store"
    assert "immutable" in ui_client.get("/assets/main-abc123.js").headers["cache-control"]


def test_an_unbuilt_bundle_explains_itself(latch, tmp_path):
    """404 здесь бесполезен: человек увидел бы пустое окно и не узнал, чего не
    хватает. Вместо этого — страница с командой сборки."""
    app = create_app(latch, security=ServerSecurity(token=TOKEN),
                     ui=tmp_path / "ничего-нет")
    with TestClient(app, base_url="http://127.0.0.1:7777") as c:
        r = c.get("/")
        assert r.status_code == 200
        assert "не собрано" in r.text and "npm run build" in r.text
        # Сервер при этом полностью работоспособен.
        assert c.get("/api/ops", headers=auth()).status_code == 200


# ------------------------------------------------------------- кто затеял
def test_the_actor_is_recorded_and_broadcast(client, latch):
    """Человек должен видеть, что из происходящего сделал он, а что — агент.
    Заголовок ничего не РАЗРЕШАЕТ, поэтому подделка им ничего не даёт: он
    влияет только на подпись в ленте."""
    r = client.post("/api/ops/datum_status",
                    headers={**auth(True), "X-Datum-Actor": "agent"}, json={})
    assert r.json()["actor"] == "agent"

    jobs = client.get("/api/jobs", headers=auth()).json()["jobs"]
    assert jobs[-1]["actor"] == "agent"


def test_an_unknown_actor_becomes_external(client):
    """Чужой процесс с токеном — не человек и не наш агент. Пусть так и
    подписывается, вместо того чтобы притворяться одним из них."""
    # Значение ASCII: HTTP не допускает иного, и клиент с кириллицей просто не
    # отправит запрос. Устойчивость actor_of к не-ASCII проверена отдельно.
    r = client.post("/api/ops/datum_status",
                    headers={**auth(True), "X-Datum-Actor": "somebody"}, json={})
    assert r.json()["actor"] == "external"

    r = client.post("/api/ops/datum_status", headers=auth(True), json={})
    assert r.json()["actor"] == "external"


def test_actor_of_survives_anything():
    """Сырой сокет может прислать что угодно; разбор подписи не должен падать."""
    from datum.server.security import actor_of

    for value in ("", "AGENT", " user ", "кириллица", "x" * 500, None):
        assert actor_of({"x-datum-actor": value}) in {"user", "agent", "external"}


def test_events_carry_the_actor(client, latch):
    """Иначе в ленте не отличить сборку, затеянную человеком, от сборки,
    затеянной агентом, — а это ровно то, ради чего лента и нужна."""
    with client.websocket_connect(WS) as ws:
        ws.send_json({"token": TOKEN})
        ws.receive_json()
        client.post("/api/ops/part_build",
                    headers={**auth(True), "X-Datum-Actor": "agent"},
                    json={"project": str(latch)})
        seen = []
        for _ in range(12):
            event = ws.receive_json()
            seen.append(event)
            if event["t"] == "job.done":
                break
    kinds = {e["t"]: e.get("actor") for e in seen}
    assert kinds.get("job.submitted") == "agent"
    assert kinds.get("geom.updated") == "agent"
    assert kinds.get("job.done") == "agent"


# ------------------------------------------------ потолок одновременных заданий
def test_isolated_jobs_queue_beyond_the_worker_limit(slow_project):
    """Число процессов сборки ограничено: агент, насыпавший десяток заданий,
    раньше упирал в память всю машину."""
    import asyncio

    from datum.server.events import EventHub
    from datum.server.jobs import JobManager
    from datum.tools.registry import OPS

    async def scenario():
        manager = JobManager(EventHub(), max_workers=1)
        spec = OPS["part_build"]
        jobs = [manager.submit(spec, {"project": str(slow_project)}) for _ in range(3)]
        for _ in range(200):
            if any(j.status == "running" for j in jobs):
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)
        statuses = sorted(j.status for j in jobs)
        # Ожидающие отменяются из очереди, не дожидаясь слота.
        for j in jobs:
            manager.cancel(j.id)
        await manager.shutdown()
        return statuses, [j.status for j in jobs]

    running_then, finally_ = asyncio.run(scenario())
    assert running_then == ["pending", "pending", "running"], running_then
    assert all(s == "cancelled" for s in finally_), finally_



# ------------------------------------------------ аргументы и проект проверяются
def test_arguments_are_checked_like_mcp_does(client):
    """REST раньше передавал тело как есть: строка вместо списка деталей
    проходила списком букв, лишний ключ ронял операцию изнутри."""
    for args, word in (({"parts": "a"}, "parts"), ({"bogus": 1}, "bogus"),
                       ({"tag": 123}, "tag")):
        op = "snapshot_create" if "tag" in args else "part_build"
        r = client.post(f"/api/ops/{op}", headers=auth(True), json=args)
        assert r.status_code == 400 and word in r.json()["error"], (args, r.json())


def test_a_foreign_project_is_refused(client, tmp_path):
    """Путь к другому проекту собирал ЧУЖОЙ проект и разослал бы окну его
    геометрию под видом своего документа."""
    r = client.post("/api/ops/part_build", headers=auth(True),
                    json={"project": str(tmp_path)})
    assert r.status_code == 400 and "открыт на проекте" in r.json()["error"]


def test_the_project_may_be_omitted(client, latch):
    r = client.post("/api/ops/project_info", headers=auth(True), json={})
    assert r.status_code == 200 and r.json()["status"] == "done"


# ------------------------------------------------ правка файла видна окну
def test_an_edit_on_disk_reaches_the_window(tmp_path):
    """Агент правит файлы своими Edit, человек — редактором. Раньше окно
    узнавало об этом, только когда нажимали «Собрать»."""
    import shutil

    root = tmp_path / "latch"
    shutil.copytree(Path(__file__).resolve().parents[3] / "examples" / "latch", root,
                    ignore=shutil.ignore_patterns(".datum"))
    app = create_app(root, security=ServerSecurity(token=TOKEN))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        with c.websocket_connect(WS, headers={"origin": "http://127.0.0.1"}) as ws:
            ws.send_json({"token": TOKEN})
            assert ws.receive_json()["t"] == "hello"
            time.sleep(0.5)                       # наблюдатель поднимается
            params = root / "params" / "base.yaml"
            params.write_text(params.read_text(encoding="utf-8").replace(
                "value: 0.30", "value: 0.25"), encoding="utf-8")
            (root / ".datum").mkdir(exist_ok=True)
            (root / ".datum" / "мусор.json").write_text("{}", encoding="utf-8")
            # Первая пачка после подъёма наблюдателя может быть НЕ про нашу
            # правку: FSEvents на macOS отдаёт накопившиеся события каталога
            # разом, и в неё попадает то, что распаковка проекта только что
            # записала (`parts/*.py`). Ждём пачку, в которой есть наша правка,
            # а проверяем по всем сразу — так и задумано: кеша `.datum/` в ленте
            # не должно быть НИ В ОДНОЙ.
            seen: list[str] = []
            for _ in range(10):
                event = ws.receive_json()
                assert event["t"] == "doc.changed", event
                seen += event["paths"]
                if "params/base.yaml" in event["paths"]:
                    break
            assert "params/base.yaml" in seen, seen
            assert not [p for p in seen if p.startswith(".datum/")], \
                "кеш .datum/ не событие"


def test_a_refused_isolated_operation_is_failed_in_the_feed(client):
    """Найдено обзором: изолированная операция, отказавшая («нет детали»),
    значилась в ленте «выполнено» — у 29 операций из 42."""
    r = client.post("/api/ops/part_place", json={"part": "нет_такой", "move": [1, 0, 0]},
                    headers=auth(mutating=True))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"].get("error") and body["status"] == "failed", body
