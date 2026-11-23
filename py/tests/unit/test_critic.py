"""ИИ-критик: когда хук не даёт закончить, сколько кругов, и что у критика в руках."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from datum.doc import critic

REPO = Path(__file__).resolve().parents[3]
PLUGIN = REPO / "plugin"


def _touch_later(path: Path, text: str) -> None:
    """Записать файл заметно позже прежних: mtime сравнивается."""
    time.sleep(0.02)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    t = time.time() + 1
    os.utime(path, (t, t))


def _export(root: Path) -> None:
    _touch_later(root / "output" / "кубик.3mf", "выгрузка")


def _edit(root: Path, n: int) -> None:
    part = root / "parts" / "cube.py"
    _touch_later(part, part.read_text(encoding="utf-8") + f"\n# правка {n}\n")
    # выгрузка теперь старше конструкции — снова «не выгружено»


def test_the_fingerprint_is_the_design_not_the_notes(cube):
    before = critic.fingerprint(cube)
    (cube / "NOTES.md").write_text("рассуждение\n", encoding="utf-8")
    (cube / "output").mkdir(exist_ok=True)
    (cube / "output" / "x.stl").write_text("…", encoding="utf-8")
    assert critic.fingerprint(cube) == before
    _edit(cube, 1)
    assert critic.fingerprint(cube) != before


def test_work_in_progress_is_not_stopped(cube, tmp_path):
    """Этап («покажу вид и спрошу») — без выгрузки: хук молчит. Пустой проект — тоже."""
    assert critic.gate_stop(cube) is None
    empty = tmp_path / "пусто"
    (empty / "parts").mkdir(parents=True)
    (empty / "datum.toml").write_text("schema = 1\n", encoding="utf-8")
    _touch_later(empty / "output" / "a.stl", "…")
    assert critic.gate_stop(empty) is None


def test_two_rounds_and_then_the_human(cube):
    """Выгрузили → критик; «доработать» → правка → критик ещё раз; «доработать»
    снова → правка → больше не зовётся: оставшееся — человеку."""
    _export(cube)
    first = critic.gate_stop(cube)
    assert first and first["decision"] == "block" and "critic" in first["reason"]

    done = critic.record(cube, "доработать", ["стенка 1.2 мм при нагрузке"])
    assert done["review"] == 1 and "Круг 1 из 2" in done["advice"]
    assert critic.gate_stop(cube) is None          # конструкция та же, что видел критик

    _edit(cube, 1)
    again = critic.gate_stop(cube)
    assert again and "второй и последний" in again["reason"]

    done = critic.record(cube, "доработать", ["всё ещё тонко"])
    assert done["review"] == 2 and "последний" in done["advice"]
    _edit(cube, 2)
    _export(cube)
    assert critic.gate_stop(cube) is None, "после второго круга — человеку, не критику"


def test_accepted_design_is_not_reviewed_again_without_a_new_export(cube):
    _export(cube)
    assert critic.gate_stop(cube)
    critic.record(cube, "принято", [])
    _edit(cube, 1)
    assert critic.gate_stop(cube) is None


def test_the_hook_never_blocks_forever(cube):
    """Агент игнорирует указание и не зовёт критика — хук сдаётся сам."""
    _export(cube)
    blocks = [critic.gate_stop(cube) for _ in range(10)]
    assert sum(1 for b in blocks if b) == critic.MAX_BLOCKS


def test_a_new_message_from_the_human_starts_a_new_task(cube):
    _export(cube)
    critic.record(cube, "доработать", ["a"])
    critic.record(cube, "доработать", ["b"])
    critic.gate_prompt(cube)
    _edit(cube, 1)
    _export(cube)
    assert critic.gate_stop(cube), "новая задача — критик снова зовётся"
    assert critic.load_state(cube)["task"]["reviews"] == 0


def test_the_verdict_operation_wants_reasons(cube):
    from datum.tools.ops import clear_project_cache, critic_verdict

    clear_project_cache()
    bad = asyncio.run(critic_verdict(project=str(cube), verdict="доработать"))
    assert "без замечаний" in bad["error"]
    ok = asyncio.run(critic_verdict(project=str(cube), verdict="доработать",
                                    remarks=["зацеп 0.2 мм при зазоре 0.3 — НЕ ДЕРЖИТ"]))
    assert ok["review"] == 1 and "НЕ ДЕРЖИТ" in ok["report"]


def test_the_gate_command_speaks_the_hook_protocol(cube):
    """CLI-привратник: событие — JSON в stdin, решение — JSON в stdout, код 0 всегда."""
    _export(cube)
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(cube)}
    out = subprocess.run([sys.executable, "-m", "datum.cli", "critic-gate", "stop"],
                         input=json.dumps({"hook_event_name": "Stop", "stop_hook_active": False}),
                         capture_output=True, text=True, env=env, timeout=60)
    assert out.returncode == 0
    assert json.loads(out.stdout)["decision"] == "block"
    junk = subprocess.run([sys.executable, "-m", "datum.cli", "critic-gate", "stop"],
                          input="{не json", capture_output=True, text=True, env=env, timeout=60)
    assert junk.returncode == 0 and junk.stdout == ""


def test_the_plugin_wires_the_hooks_and_the_critic():
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    # SessionStart — сводка проекта в контекст (tests/unit/test_brief.py).
    assert set(hooks) == {"Stop", "UserPromptSubmit", "SessionStart"}
    gate = PLUGIN / "hooks" / "critic-gate"
    assert gate.is_file() and os.access(gate, os.X_OK)
    for name in ("Stop", "UserPromptSubmit"):
        assert "${CLAUDE_PLUGIN_ROOT}/hooks/critic-gate" in hooks[name][0]["hooks"][0]["command"]


def test_the_critic_only_reads():
    """Инструменты критика существуют и ничего не меняют — кроме записи вердикта."""
    import datum.tools.ops  # noqa: F401 — регистрирует операции
    from datum.tools.registry import OPS

    text = (PLUGIN / "agents" / "critic.md").read_text(encoding="utf-8")
    front = text.split("---")[1]
    tools = [t.strip() for t in front.split("tools:")[1].splitlines()[0].split(",")]
    assert {"Read", "Grep", "Glob"} <= set(tools)
    assert not {"Edit", "Write", "Bash"} & set(tools)
    datum_tools = [t.removeprefix("mcp__datum__") for t in tools if t.startswith("mcp__datum__")]
    assert "critic_verdict" in datum_tools
    for name in datum_tools:
        assert name in OPS, f"у критика несуществующий инструмент {name}"
        if name != "critic_verdict":
            assert OPS[name].danger == "safe", f"{name} меняет проект — критику нельзя"


def test_the_auditor_only_reads_and_never_writes_the_verdict():
    """Проверяющий команды /datum:audit: только читающие инструменты, и
    вердикта критика он не пишет — аудит кругов критика не тратит."""
    import datum.tools.ops  # noqa: F401
    from datum.tools.registry import OPS

    text = (PLUGIN / "agents" / "auditor.md").read_text(encoding="utf-8")
    front = text.split("---")[1]
    assert "name: auditor" in front
    tools = [t.strip() for t in front.split("tools:")[1].splitlines()[0].split(",")]
    assert not {"Edit", "Write", "Bash", "Agent"} & set(tools)
    datum_tools = [t.removeprefix("mcp__datum__") for t in tools if t.startswith("mcp__datum__")]
    assert "critic_verdict" not in datum_tools
    assert {"project_brief", "check_run", "section", "script_run"} <= set(datum_tools)
    for name in datum_tools:
        assert name in OPS, f"у проверяющего несуществующий инструмент {name}"
        assert OPS[name].danger == "safe", f"{name} меняет проект — проверяющему нельзя"
    command = (PLUGIN / "commands" / "audit.md").read_text(encoding="utf-8")
    assert "datum:auditor" in command and "$ARGUMENTS" in command
    assert "перепроверьте САМИ" in command and "Не больше ПЯТИ" in command


# ------------------------------------------------------------------ чьё сообщение
#: Что настоящий Claude Code (2.x) передал хуку UserPromptSubmit, когда фоновый
#: субагент закончил: замерено в этой работе (пути укорочены).
TASK_NOTIFICATION = (
    "<task-notification>\n<task-id>abc29f263d2c39b12</task-id>\n"
    "<tool-use-id>toolu_011tFkSirQtfkaVvCsQqXnpX</tool-use-id>\n"
    "<output-file>/tmp/…/tasks/abc29f263d2c39b12.output</output-file>\n"
    "<status>completed</status>\n<summary>Agent \"Reply PONG\" finished</summary>\n"
    "<note>A task-notification fires each time this agent stops …</note>\n"
    "<result>PONG</result>\n<usage><subagent_tokens>11246</subagent_tokens></usage>\n"
    "</task-notification>")


def test_a_background_agent_report_is_not_the_human():
    assert critic.is_user_prompt("Сделай крышку толще")
    assert not critic.is_user_prompt(TASK_NOTIFICATION)
    assert not critic.is_user_prompt("\n  " + TASK_NOTIFICATION + "\n")
    # Начинается с тега, но дальше текст — человек; упоминание тега — тоже.
    assert critic.is_user_prompt("<b>смотри</b> вот тут поправь")
    assert critic.is_user_prompt("почему <task-notification> пришёл дважды?")
    # Вставка целиком — человек (Claude Code заворачивает её в pasted_content).
    assert critic.is_user_prompt('<pasted_content id="1">лог печати</pasted_content>')
    assert critic.is_user_prompt("")


def test_a_critic_finishing_in_the_background_does_not_reset_its_own_limit(cube):
    """Ловушка из Kadr, подтверждённая замером: хук срабатывает и на доклад
    фонового агента. Прежде он обнулял круги — и критик в фоне снимал свой
    же предел, а Stop-хук звал его снова и снова."""
    _export(cube)
    critic.record(cube, "доработать", ["a"])
    critic.gate_prompt(cube, TASK_NOTIFICATION)
    critic.record(cube, "доработать", ["b"])
    critic.gate_prompt(cube, TASK_NOTIFICATION)
    assert critic.load_state(cube)["task"]["reviews"] == 2
    _edit(cube, 1)
    _export(cube)
    assert critic.gate_stop(cube) is None, "после двух кругов — человеку, а не критику"
    critic.gate_prompt(cube, "спасибо, теперь сделай ручку")    # а человек — сбрасывает
    assert critic.load_state(cube)["task"]["reviews"] == 0


def test_the_plugin_hook_passes_the_prompt_through(cube):
    """Весь путь, как у Claude Code: скрипт хука плагина, JSON в stdin."""
    critic.record(cube, "доработать", ["a"])
    hook = PLUGIN / "hooks" / "critic-gate"
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(cube), "CLAUDE_PLUGIN_ROOT": str(PLUGIN)}

    def submit(prompt: str) -> None:
        out = subprocess.run([str(hook), "prompt"], capture_output=True, text=True, env=env,
                             input=json.dumps({"hook_event_name": "UserPromptSubmit",
                                               "prompt": prompt}), timeout=60)
        assert out.returncode == 0 and out.stdout == ""      # вывод хука ушёл бы в разговор

    submit(TASK_NOTIFICATION)
    assert critic.load_state(cube)["task"]["reviews"] == 1
    submit("сделай стенку 2 мм")
    assert critic.load_state(cube)["task"]["reviews"] == 0


# --------------------------------------------- аудит 2026-11-21: хук и круги
def test_an_old_export_does_not_block_an_unrelated_turn(cube):
    """Проект выгружен давно; человек спрашивает «какая толщина стенки?» — хук
    блокировал ход «запустите критика», хотя в этой задаче ничего не выгружали."""
    _export(cube)
    old = time.time() - 30 * 86400
    for path in (cube / "output").iterdir():
        os.utime(path, (old, old))
    for path in (cube / "parts").iterdir():
        os.utime(path, (old - 60, old - 60))
    for path in (cube / "params").iterdir():
        os.utime(path, (old - 60, old - 60))
    critic.gate_prompt(cube, "какая толщина стенки?")
    assert critic.gate_stop(cube) is None
    # А выгрузка В ЭТОЙ задаче — по-прежнему повод позвать критика.
    _export(cube)
    blocked = critic.gate_stop(cube)
    assert blocked and "критик его ещё не видел" in blocked["reason"]


def test_thanks_after_the_rounds_is_not_a_third_round(cube):
    critic.gate_prompt(cube, "сделай и выгрузи")
    _export(cube)
    critic.record(cube, "доработать", ["стенка 0.8 мм у защёлки"])
    _edit(cube, 1)
    _export(cube)
    critic.record(cube, "доработать", ["всё ещё тонко"])
    time.sleep(1.2)                      # выгрузка осталась в прошлой задаче
    critic.gate_prompt(cube, "спасибо")
    assert critic.gate_stop(cube) is None


def test_the_second_round_is_told_what_the_first_one_asked(cube):
    critic.gate_prompt(cube, "сделай и выгрузи")
    _export(cube)
    critic.record(cube, "доработать", ["стенка 0.8 мм у защёлки", "нет фаски захода"])
    _edit(cube, 1)
    blocked = critic.gate_stop(cube)
    assert blocked and "второй и последний" in blocked["reason"]
    assert "1) стенка 0.8 мм у защёлки" in blocked["reason"]
    assert "2) нет фаски захода" in blocked["reason"]
    text = (PLUGIN / "agents" / "critic.md").read_text(encoding="utf-8")
    assert "Если это второй круг" in text and ".datum/critic.json" in text
