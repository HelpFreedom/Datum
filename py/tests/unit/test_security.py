"""Граница доверия локального сервера.

Правила проверяются по отдельности: каждое закрывает свой способ обойти
петлевую привязку, и молчаливое ослабление любого из них должно ронять тест.
"""

from __future__ import annotations

import pytest
from dataclasses import fields

from datum.server.security import (CLIENT_HEADER, TOKEN_HEADER, ServerSecurity,
                                   host_is_allowed, hostname_only, new_token)


@pytest.fixture
def sec():
    return ServerSecurity(token="tok-ABC123")


def hdr(**kw) -> dict:
    return {k.replace("_", "-").lower(): v for k, v in kw.items()}


# ------------------------------------------------------------------ имя из Host
@pytest.mark.parametrize("value,expected", [
    ("127.0.0.1:7777", "127.0.0.1"),
    ("localhost", "localhost"),
    ("LocalHost:80", "localhost"),
    ("[::1]:7777", "::1"),
    ("[::1]", "::1"),
    ("evil.example.com:7777", "evil.example.com"),
    ("", ""),
    (None, ""),
])
def test_hostname_only(value, expected):
    assert hostname_only(value) == expected


def test_host_check_blocks_dns_rebinding():
    assert host_is_allowed("127.0.0.1:7777")
    assert host_is_allowed("localhost")
    assert host_is_allowed("[::1]:9")
    assert not host_is_allowed("evil.example.com")
    assert not host_is_allowed("datum.attacker.net:7777")


def test_absent_host_is_allowed():
    """Его не шлют клиенты HTTP/1.0, а браузер — ради которого правило и
    существует — шлёт всегда."""
    assert host_is_allowed(None)
    assert host_is_allowed("")


def test_port_is_ignored():
    assert host_is_allowed("127.0.0.1:12345")


def test_the_rule_has_no_off_switch():
    """Раньше проверку снимала привязка не к петле. Настройки больше нет:
    ослабить охрану аргументом невозможно, а `serve()` не поднимется нигде,
    кроме петли (см. test_serve_refuses_to_leave_the_loopback)."""
    assert not host_is_allowed("192.168.1.10")
    assert not host_is_allowed("что.угодно")
    assert "bound_host" not in {f.name for f in fields(ServerSecurity)}


# ------------------------------------------------------------------ токен
def test_token_is_required(sec):
    ok, why = sec.check("GET", "/api/doc", hdr(host="127.0.0.1:1"))
    assert not ok and TOKEN_HEADER in why


def test_wrong_token_is_refused(sec):
    ok, _ = sec.check("GET", "/api/doc",
                      hdr(host="127.0.0.1:1", **{TOKEN_HEADER: "не тот"}))
    assert not ok


def test_correct_token_passes(sec):
    ok, why = sec.check("GET", "/api/doc",
                        hdr(host="127.0.0.1:1", **{TOKEN_HEADER: "tok-ABC123"}))
    assert ok, why


def test_healthz_needs_no_token(sec):
    ok, _ = sec.check("GET", "/healthz", hdr(host="127.0.0.1:1"))
    assert ok


def test_token_comparison_is_constant_time(sec):
    """Обычное сравнение строк выдаёт длину совпавшего префикса временем."""
    import inspect

    src = inspect.getsource(type(sec).token_ok)
    assert "compare_digest" in src
    assert sec.token_ok("tok-ABC123")
    assert not sec.token_ok("tok-ABC12")
    assert not sec.token_ok(None)


def test_non_ascii_token_header_is_refused_not_crashed(sec):
    """hmac.compare_digest на строках с не-ASCII бросает TypeError.

    Заголовок присылает клиент, поэтому кириллица в нём — это не «странный
    случай», а способ получить 500 вместо отказа.
    """
    assert sec.token_ok("кириллица") is False
    ok, _ = sec.check("GET", "/api/doc",
                      hdr(host="127.0.0.1:1", **{TOKEN_HEADER: "кириллица"}))
    assert not ok


def test_tokens_are_unguessable():
    a, b = new_token(), new_token()
    assert a != b and len(a) >= 40


# ------------------------------------------------------------------ изменяющие запросы
def test_mutating_requests_need_the_client_header(sec):
    base = hdr(host="127.0.0.1:1", **{TOKEN_HEADER: "tok-ABC123"})
    ok, why = sec.check("POST", "/api/tools/check_run", base)
    assert not ok and CLIENT_HEADER in why

    ok, _ = sec.check("POST", "/api/tools/check_run", {**base, CLIENT_HEADER: "1"})
    assert ok


def test_safe_methods_do_not_need_it(sec):
    base = hdr(host="127.0.0.1:1", **{TOKEN_HEADER: "tok-ABC123"})
    for method in ("GET", "HEAD", "OPTIONS"):
        assert sec.check(method, "/obj/x.glb", base)[0]


def test_host_is_checked_before_anything_else(sec):
    """Даже с верным токеном чужой Host не проходит."""
    ok, why = sec.check("GET", "/api/doc",
                        hdr(host="evil.example.com", **{TOKEN_HEADER: "tok-ABC123"}))
    assert not ok and "перепривязк" in why


# ------------------------------------------------------------------ WebSocket
def test_origin_check(sec):
    assert sec.origin_ok("http://127.0.0.1:7777")
    assert sec.origin_ok("http://localhost:3000")
    assert sec.origin_ok(None), "не-браузерные клиенты Origin не шлют"
    assert not sec.origin_ok("https://evil.example.com")
