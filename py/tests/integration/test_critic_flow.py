"""Критик целиком — без обращений к API: настоящий сервер Datum, настоящий
MCP-мост (`datum mcp`, тонкий клиент сервера) и настоящий хук плагина
(`plugin/hooks/critic-gate`), а claude подставной — этот тест делает ровно то,
что делал бы он: шлёт хуку события и зовёт инструменты.

Идея — из Kadr (e2e53: критик через настоящий мост с подставным claude).
Держится главное: выгруженное без критика не сдаётся, кругов не больше двух,
фоновый критик своим докладом свой предел не снимает, хук не держит вечно, а
новое сообщение человека начинает задачу заново.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.integration.test_lifecycle import serve, wait_for

ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugin"
HOOK = PLUGIN / "hooks" / "critic-gate"

#: Доклад фонового агента, как его передал хуку настоящий Claude Code.
TASK_NOTIFICATION = ("<task-notification>\n<task-id>a1</task-id>\n<status>completed</status>\n"
                     "<result>вердикт записан</result>\n</task-notification>")


def _hook(project: Path, event: str, payload: dict) -> dict | None:
    out = subprocess.run([str(HOOK), event], input=json.dumps(payload), capture_output=True,
                         text=True, timeout=60,
                         env={**os.environ, "CLAUDE_PROJECT_DIR": str(project),
                              "CLAUDE_PLUGIN_ROOT": str(PLUGIN)})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout) if out.stdout.strip() else None


def _say(project: Path, prompt: str) -> None:
    assert _hook(project, "prompt", {"hook_event_name": "UserPromptSubmit",
                                     "prompt": prompt}) is None


def _stop(project: Path) -> str | None:
    """Агент заканчивает ход: причина, по которой хук не даёт, или None."""
    decision = _hook(project, "stop", {"hook_event_name": "Stop", "stop_hook_active": False})
    return decision["reason"] if decision else None


def _edit(project: Path, n: int) -> None:
    time.sleep(0.05)
    lid = project / "parts" / "lid.py"
    lid.write_text(lid.read_text(encoding="utf-8") + f"\n# правка {n}\n", encoding="utf-8")
    t = time.time() - 5                              # конструкция старше будущей выгрузки
    os.utime(lid, (t, t))


def test_the_critic_flow_over_the_real_bridge(latch, tmp_path):
    handshake = tmp_path / "hs.json"
    process = serve(latch, handshake)
    try:
        wait_for(handshake, process)
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "datum.cli", "mcp", "--stdio"],
            cwd=str(ROOT / "py"),
            env={**os.environ, "DATUM_HANDSHAKE": str(handshake)})

        async def session():
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()

                    async def call(name: str, **args) -> dict:
                        res = await s.call_tool(name, {"project": str(latch), **args})
                        text = res.content[0].text if res.content else "{}"
                        data = json.loads(text)
                        assert not data.get("error"), (name, data.get("error"))
                        return data

                    _say(latch, "сделай корпус с защёлкой и выгрузи")
                    assert _stop(latch) is None, "этап без выгрузки хук не трогает"

                    await call("export", checks=False)
                    reason = _stop(latch)
                    assert reason and "datum:critic" in reason, reason

                    # Критик (субагент в фоне) смотрит и просит доработать.
                    await call("critic_verdict", verdict="доработать",
                               remarks=["губка 0.8 мм при зазоре 0.3 — держит впритык"])
                    _say(latch, TASK_NOTIFICATION)       # его доклад — не человек
                    assert _stop(latch) is None, "критик видел эту конструкцию"

                    _edit(latch, 1)
                    await call("export", checks=False)
                    reason = _stop(latch)
                    assert reason and "второй и последний" in reason, reason

                    await call("critic_verdict", verdict="доработать", remarks=["всё ещё впритык"])
                    _say(latch, TASK_NOTIFICATION)
                    _edit(latch, 2)
                    await call("export", checks=False)
                    for _ in range(5):                   # дальше — человеку, и никогда вечно
                        assert _stop(latch) is None

                    # Новое сообщение человека — новая задача: критик снова зовётся.
                    _say(latch, "хорошо, сделай губку 1 мм")
                    _edit(latch, 3)
                    await call("export", checks=False)
                    assert _stop(latch), "новая задача — критик снова"

        asyncio.run(session())
        state = json.loads((latch / ".datum" / "critic.json").read_text(encoding="utf-8"))
        assert state["task"]["reviews"] == 0 and state["last"]["verdict"] == "доработать"

        # Мост действительно шёл через живой сервер — в его ленте задания агента.
        import urllib.request

        info = json.loads(handshake.read_text(encoding="utf-8"))
        request = urllib.request.Request(
            f"http://127.0.0.1:{info['port']}/api/jobs?brief=true",
            headers={"X-Datum-Token": info["token"], "X-Datum-Client": "1"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=30) as answer:
            jobs = json.loads(answer.read().decode("utf-8"))["jobs"]
        done = [(j["op"], j["actor"]) for j in jobs if j["status"] == "done"]
        assert done.count(("critic_verdict", "agent")) == 2, done
        assert done.count(("export", "agent")) == 4, done
    finally:
        process.terminate()
        process.wait(timeout=10)
