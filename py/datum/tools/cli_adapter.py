"""Построение командной строки из реестра операций.

argparse собирается из тех же сигнатур, что и схемы MCP. Правила перевода:

  обязательный аргумент          → позиционный
  аргумент со значением по умолчанию → --ключ
  bool                            → флаг --ключ
  list[str]                       → повторяемый (позиционный nargs="*")
  list[float] / list[int]         → ОДИН токен через запятую: «30,20,40»
  dict[str, float] / dict[str, str] → повторяемый --ключ ИМЯ=ЗНАЧЕНИЕ; значение —
                                    число или строка, как объявлено в сигнатуре
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

from datum.tools.registry import OPS, ArgSpec, OpSpec

__all__ = ["build_parser", "run", "main", "EXIT_OP_ERROR"]

#: Коды возврата:
#:   0 — всё хорошо;
#:   1 — операция отработала, но результат плохой (проверки провалены);
#:   2 — неверные аргументы командной строки (так отвечает argparse);
#:   3 — операция отказала: нет проекта, деталь не собралась, файл не читается.
#: Раньше отказ операции тоже был 2 и не отличался от опечатки в ключе.
EXIT_OP_ERROR = 3


def _kv_pairs(values: list[str] | None, kind: type = float) -> dict[str, Any] | None:
    """`ИМЯ=ЗНАЧЕНИЕ` → словарь. Значение — того типа, что у словаря в сигнатуре:
    правки профиля слайсера (`dict[str, str]`) бывают и не числами — тип
    поддержек, шаблон заполнения, — и прежний разбор «всё в числа» отказывал им."""
    if not values:
        return None
    out: dict[str, Any] = {}
    for item in values:
        if "=" not in item:
            raise argparse.ArgumentTypeError(
                f"нужно ИМЯ=ЗНАЧЕНИЕ, получено {item!r}")
        k, v = item.split("=", 1)
        if kind is str:
            out[k.strip()] = v
            continue
        try:
            out[k.strip()] = float(v)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{item}: {v!r} не число")
    return out


def _map_value_type(a: ArgSpec) -> type:
    args = get_args(a.base_type)
    return str if len(args) == 2 and args[1] is str else float


def _is_list_of_str(a: ArgSpec) -> bool:
    t = a.base_type
    return get_origin(t) is list and (not get_args(t) or get_args(t)[0] is str)


def _is_list_of_number(a: ArgSpec) -> bool:
    t = a.base_type
    args = get_args(t)
    return get_origin(t) is list and bool(args) and args[0] in (float, int)


def _vector(text: str) -> list[float]:
    """«30,20,40» или «30 20 40» → [30.0, 20.0, 40.0].

    Числовой список — ОДИН токен, а не nargs="*". Иначе две таких величины
    подряд (точка и направление у зонда) argparse развести не может: первая
    забирает всё. Через запятую двусмысленности нет вовсе.
    """
    parts = [v for v in re.split(r"[,\s]+", str(text).strip()) if v]
    try:
        return [float(v) for v in parts]
    except ValueError as e:
        raise argparse.ArgumentTypeError(
            f"ожидались числа через запятую, получено {text!r}") from e


def _is_map(a: ArgSpec) -> bool:
    t = a.base_type
    return get_origin(t) is dict


def _add_argument(sp: argparse.ArgumentParser, a: ArgSpec) -> None:
    help_text = a.help or ""
    if _is_map(a):
        sp.add_argument(f"--{a.name.replace('_', '-')}", dest=a.name, action="append",
                        metavar=a.metavar or "ИМЯ=ЗНАЧЕНИЕ", help=help_text)
        return
    if _is_list_of_number(a):
        if a.required:
            sp.add_argument(a.name, type=_vector,
                            metavar=a.metavar or a.name.upper(), help=help_text)
        else:
            sp.add_argument(f"--{a.name.replace('_', '-')}", dest=a.name,
                            type=_vector, default=a.default,
                            metavar=a.metavar or "X,Y,Z", help=help_text)
        return
    if _is_list_of_str(a):
        if a.required:
            sp.add_argument(a.name, nargs="*", metavar=a.metavar or a.name.upper(),
                            help=help_text)
        else:
            sp.add_argument(f"--{a.name.replace('_', '-')}", dest=a.name,
                            action="append", metavar=a.metavar or "ЗНАЧЕНИЕ",
                            help=help_text)
        return
    t = a.base_type
    if t is bool:
        if a.default is True:
            # Включённое по умолчанию выключается `--no-…`. Голый store_true
            # здесь молча давал False, если флаг не набран: умолчание операции
            # переворачивалось в командной строке.
            sp.add_argument(f"--{a.name.replace('_', '-')}", dest=a.name,
                            action=argparse.BooleanOptionalAction, default=True,
                            help=help_text)
        else:
            sp.add_argument(f"--{a.name.replace('_', '-')}", dest=a.name,
                            action="store_true", help=help_text)
        return
    # Набор значений из сигнатуры (Literal) — это choices: argparse сам назовёт
    # допустимые, а не пропустит опечатку внутрь операции.
    choices = list(get_args(t)) if get_origin(t) is Literal else None
    kind = str if choices else {int: int, float: float}.get(t, str)
    if a.required:
        sp.add_argument(a.name, type=kind, choices=choices,
                        metavar=a.metavar or a.name.upper(), help=help_text)
    else:
        sp.add_argument(f"--{a.name.replace('_', '-')}", dest=a.name, type=kind,
                        choices=choices, default=a.default,
                        metavar=a.metavar or ("{" + ",".join(choices) + "}" if choices
                                              else "ЗНАЧЕНИЕ"),
                        help=help_text + (f" (по умолчанию {a.default!r})"
                                          if a.default not in (None, "", 0.0) else ""))


def _collect(spec: OpSpec, ns: argparse.Namespace) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    for a in spec.args:
        value = getattr(ns, a.name, None)
        if _is_map(a):
            value = _kv_pairs(value, _map_value_type(a))
        if value is None and not a.required:
            continue
        kwargs[a.name] = value
    return kwargs


def build_parser() -> argparse.ArgumentParser:
    import datum.tools.ops  # noqa: F401  — объявление операций

    p = argparse.ArgumentParser(
        prog="datum",
        description="Datum — проектирование и проверка деталей для 3D-печати")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="КОМАНДА")

    for spec in sorted(OPS.values(), key=lambda s: (s.group, s.command)):
        if not spec.cli:
            continue
        sp = sub.add_parser(spec.command, help=spec.title,
                            description=spec.description)
        for a in spec.args:
            _add_argument(sp, a)
        sp.add_argument("--json", action="store_true", help="вывод в JSON")
        if spec.isolate:
            sp.add_argument("--in-process", action="store_true",
                            help="выполнить в текущем процессе, без пределов и "
                                 "отмены (для отладки)")
        sp.set_defaults(_spec=spec)

    # Серверы — не операции: они не возвращают результат, а живут до остановки.
    sp = sub.add_parser("mcp", help="MCP-сервер (дверь агента)",
                        description="Запустить сервер MCP по stdio для Claude Code.")
    # Флаг оставлен для совместимости с уже записанными конфигами: другого
    # транспорта нет, и stdio — единственный режим.
    sp.add_argument("--stdio", action="store_true", default=True,
                    help="единственный транспорт; флаг принимается для совместимости")
    sp.set_defaults(_spec=None, _server="mcp")

    sp = sub.add_parser("serve", help="локальный сервер (для интерфейса)",
                        description="Поднять локальный сервер на случайном порту "
                                    "петлевого интерфейса. Порт и токен пишутся "
                                    "в файл рукопожатия.")
    sp.add_argument("project", metavar="ПРОЕКТ")
    sp.add_argument("--port", type=int, default=0, help="0 — попросить у ядра")
    sp.add_argument("--handshake", default=None, help="куда записать порт и токен")
    sp.add_argument("--ui", default=None,
                    help="каталог собранного окна (по умолчанию apps/ui/dist)")
    sp.add_argument("--parent-pid", type=int, default=0,
                    help="умереть, если этот процесс исчезнет")
    sp.add_argument("--log-level", default="warning")
    sp.set_defaults(_spec=None, _server="serve")

    # Привратник ИИ-критика для хуков плагина (plugin/hooks): событие Claude Code
    # приходит JSON в stdin, ответ — JSON в stdout. Не операция: у хука нет
    # аргументов, только событие.
    sp = sub.add_parser("critic-gate", help="хук плагина: звать ли критика",
                        description="Stop — не дать закончить, пока критик не видел "
                                    "выгруженную конструкцию; prompt — новое сообщение "
                                    "человека, круги критика заново.")
    sp.add_argument("event", choices=["stop", "prompt"])
    sp.set_defaults(_spec=None, _server="critic-gate")

    # Сводка проекта в контекст агента — хуком SessionStart (старт сессии,
    # /clear, сжатие контекста). Тоже не операция: читает событие из stdin.
    sp = sub.add_parser("session-brief", help="хук плагина: сводка проекта в контекст",
                        description="Печатает сводку проекта (project_brief) ответом хука "
                                    "SessionStart — чтобы новая сессия и сессия после "
                                    "сжатия знали, на чём остановились.")
    sp.set_defaults(_spec=None, _server="session-brief")

    # Создание проекта — тоже не операция: у операций есть проект, а здесь его
    # ещё нет. Агенту она не нужна — он пишет файлы сам.
    sp = sub.add_parser("init", help="Новый пустой проект",
                        description="Создать пустой проект: datum.toml, параметры, "
                                    "проверки и каталог parts/ без деталей. Ничего "
                                    "не перезаписывает.")
    sp.add_argument("project", metavar="КАТАЛОГ")
    sp.add_argument("--name", default="", help="имя проекта (по умолчанию — имя каталога)")
    sp.set_defaults(_spec=None, _server="init")
    return p


def run(spec: OpSpec, ns: argparse.Namespace) -> int:
    kwargs = _collect(spec, ns)
    if spec.isolate and (sys.platform == "win32" or not getattr(ns, "in_process", False)):
        from datum.tools.isolate import run_isolated

        result = run_isolated(spec.fn, kwargs, headroom_mb=spec.memory_headroom_mb)
    else:
        result = asyncio.run(spec.fn(**kwargs))
    if getattr(ns, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    elif result.get("error"):
        # Отказ операции — в поток ошибок: иначе его не отличить от ответа,
        # перенаправив вывод в файл.
        print(result.get("report") or f"ОШИБКА: {result['error']}", file=sys.stderr)
    else:
        text = result.get("report")
        print(text if text else json.dumps(result, ensure_ascii=False, indent=2,
                                           default=str))
    if result.get("error"):
        return EXIT_OP_ERROR
    return spec.exit_code(result) if spec.exit_code else 0


def _init(project: str, name: str) -> int:
    from datum.doc.scaffold import ScaffoldError, init_project

    try:
        made = init_project(project, name)
    except ScaffoldError as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        return EXIT_OP_ERROR
    root = Path(project).expanduser().resolve()
    print(f"Проект создан: {root}")
    for path in made:
        print(f"  {path.relative_to(root)}")
    # Проверять в пустом проекте нечего: первый шаг — деталь, в окне или скриптом.
    print(f"\nДальше: окно — datum {root}  ·  деталь — скрипт в {root / 'parts'}")
    return 0


def _critic_gate(event: str) -> int:
    """Хук плагина. Никогда не мешает работе своей ошибкой: что бы ни случилось —
    код 0 и «можно» (страховка не должна становиться поломкой)."""
    import os

    from datum.doc.critic import gate_prompt, gate_stop

    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        root = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
        if event == "prompt":
            prompt = data.get("prompt")
            gate_prompt(root, prompt if isinstance(prompt, str) else None)
            return 0
        decision = gate_stop(root)
        if decision:
            print(json.dumps(decision, ensure_ascii=False))
    except Exception as e:                   # pragma: no cover — страховка
        print(f"critic-gate: {type(e).__name__}: {e}", file=sys.stderr)
    return 0


def _session_brief() -> int:
    """Хук SessionStart: сводка проекта — в контекст. Ошибка хука работе не
    мешает: код 0 и молчание."""
    import os

    from datum.doc import brief

    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        root = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
        text = brief.compose(root)
        if text:
            why = {"compact": "Контекст только что сжат", "clear": "Контекст очищен",
                   "resume": "Сессия продолжена"}.get(str(data.get("source", "")),
                                                      "Начало сессии")
            note = (f"{why}. Сводка проекта Datum (собрана из файлов, код проекта не "
                    f"исполнялся):\n\n{text}\n\nЗаканчивая этап, запишите решения "
                    f"операцией note_add — следующая сессия начнёт с них.")
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "SessionStart", "additionalContext": note}},
                ensure_ascii=False))
    except Exception as e:                   # pragma: no cover — страховка
        print(f"session-brief: {type(e).__name__}: {e}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    spec: OpSpec | None = getattr(ns, "_spec", None)
    if spec is None:
        which = getattr(ns, "_server", "mcp")
        if which == "init":
            return _init(ns.project, ns.name)
        if which == "critic-gate":
            return _critic_gate(ns.event)
        if which == "session-brief":
            return _session_brief()
        if which == "serve":
            from datum.server.run import serve

            return serve(ns.project, port=ns.port, handshake=ns.handshake,
                         ui=ns.ui, parent_pid=ns.parent_pid,
                         log_level=ns.log_level)
        from datum.tools.mcp_adapter import main as mcp_main

        return mcp_main()
    try:
        return run(spec, ns)
    except argparse.ArgumentTypeError as e:
        print(f"ошибка аргументов: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        # Процесс сборки к этому моменту уже снят (см. worker.wait_or_cancel).
        print("прервано", file=sys.stderr)
        return 130
