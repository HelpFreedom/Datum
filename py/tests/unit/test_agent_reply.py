"""Ответ агенту влезает в его предел (`tools/agent_reply.py`).

Найдено на Gear Cube: `check_run` из 102 проверок — 116 тыс. знаков в виде,
в каком его шлёт MCP, и Claude Code положил ответ в файл вместо того, чтобы
показать. Сокращается только ответ MCP; окно и CLI получают целиком.
"""

from __future__ import annotations

import asyncio

from datum.tools.agent_reply import AGENT_REPLY_MAX, fit_for_agent, reply_size

DEPS = [f"PARAM_{i:03d}" for i in range(90)]


def _run(n_pass: int, n_fail: int = 1) -> dict:
    def result(i: int, status: str) -> dict:
        return {"id": f"деталь_{i}.проверка", "title": "Проверка", "group": "printing",
                "status": status, "message": f"деталь_{i}: значение рядом с порогом " * 3,
                "value": 1.0, "unit": "мм", "op": ">=", "threshold": 0.5, "margin": 1.0,
                "depends_on": DEPS, "inputs": [f"деталь_{i}"], "severity": "blocking",
                "duration_ms": 12.5, "evidence": [{"kind": "png", "url": "/obj/x.png"}]}
    results = ([result(i, "pass") for i in range(n_pass)]
               + [result(n_pass + i, "fail") for i in range(n_fail)])
    return {"ok": n_fail == 0, "n_pass": n_pass, "n_fail": n_fail, "results": results,
            "worst": results[-1], "report": "\n".join(r["message"] for r in results)}


def test_a_small_reply_is_untouched():
    reply = _run(5)
    assert fit_for_agent(reply) is reply


def test_a_large_run_fits_and_keeps_every_failure_whole():
    reply = _run(120, n_fail=3)
    assert reply_size(reply) > 3 * AGENT_REPLY_MAX
    out = fit_for_agent(reply)
    assert reply_size(out) <= AGENT_REPLY_MAX + 1000          # + сама пометка
    failed = [r for r in out["results"] if r["status"] == "fail"]
    assert len(failed) == 3 and all(r["message"] and r["evidence"] for r in failed)
    assert all(r["depends_on_n"] == 90 for r in failed)
    assert out["report"] == reply["report"] and out["n_pass"] == 120
    assert "сокращён" in out["agent_note"]
    # Исходный ответ не тронут: его же может получить и другой потребитель.
    assert reply["results"][0]["depends_on"] == DEPS


def test_the_steps_stop_as_soon_as_it_fits():
    """Первый шаг — только служебное у прошедших: ответ, которому этого
    хватило, сохраняет сообщения всех проверок."""
    reply = _run(40)
    size = reply_size(reply)
    out = fit_for_agent(reply, limit=size - 1000)
    passed = [r for r in out["results"] if r["status"] == "pass"]
    assert all("message" in r and "depends_on" not in r for r in passed)
    assert out["results"][-1]["depends_on"] == DEPS
    assert "только числом" not in out["agent_note"]          # один шаг


def test_in_the_end_passed_checks_leave_the_list_but_not_the_report():
    reply = _run(400)
    out = fit_for_agent(reply, limit=5_000)
    assert out["results_omitted"] == 400
    assert [r["status"] for r in out["results"]] == ["fail"]
    assert out["report"] == reply["report"]


def test_build_parts_are_trimmed_by_their_ok_flag():
    parts = {f"д{i}": {"name": f"д{i}", "ok": True, "depends_on": DEPS,
                       "topology": {"volume_mm3": 1.0}} for i in range(200)}
    parts["сломанная"] = {"name": "сломанная", "ok": False, "error": "нет", "depends_on": DEPS}
    out = fit_for_agent({"ok": False, "parts": parts, "report": "…"})
    assert reply_size(out) <= AGENT_REPLY_MAX + 1000
    assert out["parts"]["сломанная"]["error"] == "нет"
    assert "depends_on" not in out["parts"]["д0"]


def test_the_mcp_wrapper_fits_the_reply():
    """Обёртка MCP (`routed_callable`) сокращает; сама операция — нет."""
    from dataclasses import replace

    import datum.tools.ops  # noqa: F401  — объявление операций
    from datum.tools import remote
    from datum.tools.registry import OPS

    big = _run(150)

    async def fake():                              # без проекта: зовётся у себя
        return big

    spec = replace(OPS["check_list"], fn=fake, isolate=False)
    out = asyncio.run(remote.routed_callable(spec)())
    assert "agent_note" in out and reply_size(out) <= AGENT_REPLY_MAX + 1000
    assert asyncio.run(fake()) is big and "agent_note" not in big
