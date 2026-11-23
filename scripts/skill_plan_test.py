#!/usr/bin/env python3
"""Проверка навыка Datum настоящим claude: загружает ли он навык, читает ли
нужный справочник и в каком порядке собирается действовать.

Тест на числах (`py/tests/unit/test_skill.py`) держит бюджет и ссылки, но не
отвечает на главный вопрос — пользуется ли агент навыком так, как задумано.
Здесь claude получает задачу из жизни Datum и просит составить ПЛАН, ничего не
делая: доступны только плагин Datum, `Skill` и `Read`; настройки и навыки
пользователя отключены (`--setting-sources ""`), так что виден ровно навык из
рабочего дерева. Оценка — по журналу (`stream-json`): какие навыки вызваны,
какие файлы прочитаны, и по тексту плана — какие шаги и в каком порядке.

Идея — из Kadr (`skill-plan-test.mjs`). СТОИТ ЛИМИТА CLAUDE: четыре задачи,
каждая — разговор с моделью. Запускать после правки навыка, а не каждый раз.

    .venv/bin/python scripts/skill_plan_test.py [--only ID] [--model sonnet]

Код 0 — всё прошло, 1 — что-то нет, 2 — claude не запустился.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin"
REFERENCES = sorted(p.name for p in (PLUGIN / "skills" / "datum" / "references").glob("*.md"))

ASK = ("Ты работаешь в проекте Datum (в текущем каталоге есть datum.toml). Задача "
       "человека: «{task}». НИЧЕГО НЕ ДЕЛАЙ и не задавай вопросов — составь план "
       "работ нумерованным списком: какие инструменты Datum и в каком порядке ты "
       "применишь и что проверишь. Пользуйся навыком Datum и его справочниками.")


@dataclass
class Case:
    id: str
    task: str
    #: справочник, который обязан быть прочитан (хотя бы один из)
    reads: tuple[str, ...]
    #: слова плана, которые обязаны встретиться В ЭТОМ ПОРЯДКЕ (каждое — варианты)
    order: tuple[tuple[str, ...], ...]
    #: что в плане обязано быть (каждое — варианты)
    must: tuple[tuple[str, ...], ...] = ()


CASES = [
    Case("коробка",
         "коробка под плату Arduino Uno с крышкой на защёлке, печатать на нашем принтере",
         reads=("build.md", "assembly.md", "workflow.md"),
         order=(("assumed", "sweep_assumed"), ("part_view",), ("check_run",),
                ("critic", "критик"), ("export",)),
         must=(("assembly.yaml",), ("snap",), ("insert",), ("escape",),
               ("centre_on_bed",))),
    Case("ложемент",
         "ложемент под компьютерную мышь по скану mouse.stl, мышь вставляется сверху",
         reads=("models.md",),
         order=(("import_model", "import_to_bed"), ("footprint", "insertion_pocket"),
                ("check_run", "assembly_path", "rigid"))),
    Case("ось",
         "ось шарнира откидной крышки, на крышку опираются с силой до 50 кг — выдержит ли",
         reads=("mechanics.md", "strength.md"),
         order=(("pin_joint",),),
         must=(("изгиб",), ("срез",), ("смяти",))),
    Case("надпись",
         "выпуклая надпись «Datum» на крышке шрифтом Liberation Sans",
         reads=("build.md", "api.md"),
         order=(("fonts_list", "font_add"), ("text_z",)),
         must=(("union",),)),
    Case("механизм",
         "поворотная рука-стрела на двух сервах MG996R ставит стакан 200 г на полку; "
         "стоит на столе без крепления — хватит ли серв и не опрокинется ли",
         reads=("simulation.md",),
         order=(("@mechanism", "mechanism"), ("sim_run", "sim_drive")),
         must=(("print=", "настройк"), ("sim_base", "опрокид"), ("resting",))),
    Case("привод",
         "подъёмник: червячный мотор-редуктор JGY-370 12 В крутит ходовой винт T8 и "
         "поднимает каретку с грузом 1 кг; хватит ли мотора, удержит ли груз без тока, "
         "какой люфт будет у каретки",
         reads=("drives.md",),
         order=(("@mechanism", "mechanism"), ("sim_run", "sim_drive")),
         must=(("паспорт", "datasheet"), ("efficiency", "кпд"), ("самоторм", "back_efficiency"),
               ("ротор", "rotor_inertia"), ("assumed",), ("backlash", "люфт"))),
]


@dataclass
class Run:
    skills: list[str] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)
    plan: str = ""
    error: str = ""
    raw: str = ""


def parse_stream(stdout: str) -> Run:
    """Журнал `stream-json` → вызванные навыки, прочитанные справочники, план."""
    run = Run(raw=stdout)
    searches: set[str] = set()                 # id вызовов Grep/Glob
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "user":
            # Ответ поиска: какие справочники в нём показались — те и прочитаны.
            for item in event.get("message", {}).get("content", []) or []:
                if isinstance(item, dict) and item.get("tool_use_id") in searches:
                    content = item.get("content")
                    text = content if isinstance(content, str) else " ".join(
                        str(x.get("text", "")) for x in content or [] if isinstance(x, dict))
                    run.reads += [n for n in REFERENCES if f"references/{n}" in text]
        if event.get("type") == "assistant":
            for item in event.get("message", {}).get("content", []) or []:
                if item.get("type") != "tool_use":
                    continue
                args = item.get("input") or {}
                if item.get("name") == "Skill":
                    run.skills.append(str(args.get("skill") or args.get("command") or ""))
                elif item.get("name") == "Read":
                    run.reads.append(Path(str(args.get("file_path", ""))).name)
                elif item.get("name") in ("Grep", "Glob"):
                    searches.add(str(item.get("id")))
                    # Поиск по справочнику — тоже обращение к нему.
                    where = f"{args.get('path', '')} {args.get('glob', '')} {args.get('pattern', '')}"
                    run.reads += [n for n in REFERENCES if n in where]
                    if "references" in where and not any(n in where for n in REFERENCES):
                        run.reads.append("references/*")
        elif event.get("type") == "result":
            run.plan = str(event.get("result") or "")
            if event.get("is_error"):
                run.error = run.plan[:300]
    return run


def run_claude(case: Case, model: str, workdir: Path) -> Run:
    claude = shutil.which("claude")
    if claude is None:
        return Run(error="claude не найден")
    env = dict(os.environ)
    proxy = env.get("DATUM_PROXY", "http://127.0.0.1:10808")   # как у встроенного агента
    env.update(HTTPS_PROXY=proxy, HTTP_PROXY=proxy, NO_PROXY="127.0.0.1,localhost,::1")
    for name in list(env):                     # не наследовать чужую сессию Claude Code
        if name.startswith("CLAUDE_CODE_") or name == "CLAUDECODE":
            del env[name]
    cmd = [claude, "-p", ASK.format(task=case.task), "--model", model,
           "--plugin-dir", str(PLUGIN), "--setting-sources", "",
           # Набор инструментов — ТОЛЬКО читающие: `--allowedTools` лишь
           # разрешает заранее, а остальное (Bash и прочее) остаётся у модели —
           # замерено: справочник читался через `grep` в Bash, и оценка его не видела.
           "--tools", "Skill,Read,Grep,Glob", "--allowedTools", "Skill,Read,Grep,Glob",
           "--add-dir", str(PLUGIN),
           "--output-format", "stream-json", "--verbose"]
    try:
        out = subprocess.run(cmd, cwd=workdir, env=env, capture_output=True, text=True,
                             timeout=600)
    except subprocess.TimeoutExpired:
        return Run(error="claude не уложился в 10 минут")
    run = parse_stream(out.stdout)
    if not run.plan and not run.error:
        run.error = (out.stderr or "пустой ответ")[-300:]
    return run


def grade(case: Case, run: Run) -> list[tuple[str, bool, str]]:
    plan = run.plan.lower()
    checks = [("навык Datum загружен", any(s.split(":")[-1] == "datum" for s in run.skills),
               ", ".join(run.skills) or "ни одного")]
    # План — без действий, и «прочитаю build.md перед скриптом» — ровно то, что
    # велит ядро; поэтому справочник засчитывается и прочитанным, и названным.
    consulted = [r for r in case.reads if r in run.reads]
    planned = [r for r in case.reads if r.lower() in plan]
    checks.append((f"справочник {' или '.join(case.reads)}", bool(consulted or planned),
                   f"прочитан: {', '.join(sorted(set(run.reads))) or 'ничего'}; "
                   f"назван в плане: {', '.join(planned) or 'нет'}"))
    position, ordered, missing = -1, True, []
    for variants in case.order:
        found = [plan.find(v.lower(), position + 1) for v in variants]
        found = [f for f in found if f >= 0]
        if not found:
            ordered = False
            missing.append("/".join(variants))
            continue
        position = min(found)
    checks.append(("шаги плана по порядку", ordered,
                   "нет или не по порядку: " + ", ".join(missing) if missing else "да"))
    # «Запретных слов» нет намеренно: подстрока не отличает «пишу make3mf» от
    # «своего make3mf не пишу» (так агент и повторил правило навыка).
    absent = ["/".join(v) for v in case.must if not any(x.lower() in plan for x in v)]
    checks.append(("в плане есть главное", not absent,
                   "нет: " + ", ".join(absent) if absent else "да"))
    return checks


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--only", help="одна задача по id")
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--keep", metavar="КАТАЛОГ", help="сохранить планы в этот каталог")
    ns = parser.parse_args(argv)
    cases = [c for c in CASES if not ns.only or c.id == ns.only]
    failed = 0
    with tempfile.TemporaryDirectory(prefix="datum-skill-plan-") as tmp:
        for case in cases:
            work = Path(tmp) / case.id
            work.mkdir()
            subprocess.run([sys.executable, "-m", "datum.cli", "init", str(work)],
                           cwd=ROOT / "py", capture_output=True, check=True)
            run = run_claude(case, ns.model, work)
            print(f"\n== {case.id}: {case.task}")
            if run.error:
                print(f"  НЕ ЗАПУСТИЛСЯ: {run.error}")
                return 2
            for name, ok, detail in grade(case, run):
                failed += not ok
                print(f"  {'OK   ' if ok else 'ПРОВАЛ'} {name}: {detail}")
            if ns.keep:
                Path(ns.keep).mkdir(parents=True, exist_ok=True)
                plan_file = Path(ns.keep) / f"skill-plan-{case.id}.md"
                plan_file.write_text(run.plan, encoding="utf-8")
                plan_file.with_suffix(".jsonl").write_text(run.raw, encoding="utf-8")
                print(f"  план: {plan_file}")
    print("\nИТОГ:", "всё прошло" if not failed else f"не прошло: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
