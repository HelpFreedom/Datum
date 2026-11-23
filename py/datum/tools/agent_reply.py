"""Ответ операции — агенту: столько, сколько он прочтёт.

Найдено на Gear Cube (102 проверки): `check_run` вернул 78 тыс. знаков, и
Claude Code не показал ответ вовсе — положил в файл с пометкой «превышен
предел токенов», агенту пришлось разбирать его по кускам. Две трети
объёма — служебное: у каждой проверки список параметров `depends_on` (у
проверки сборки — 90 имён), а каждое сообщение повторено в `report`.

Сокращается ТОЛЬКО ответ MCP и ТОЛЬКО когда он не влезает: окно (HTTP) и
`--json` в командной строке получают его целиком. Режется сначала то, что
агенту нужно меньше всего, и шаги идут по одному, пока ответ не влезет:

  1. у прошедших — зависимости, входы, время, доказательства;
  2. зависимости у всех — остаётся их число (`depends_on_n`);
  3. прошедшие — только id, статус и запас (строки целиком — в `report`);
  4. прошедших нет вовсе — счётчики и `report` остаются.

Что срезано — сказано в `agent_note`, словами: молча урезанный ответ
выглядел бы полным.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["AGENT_REPLY_MAX", "fit_for_agent", "for_agent", "reply_size"]

#: Предел ответа агенту — знаков JSON в том виде, в каком его шлёт MCP
#: (`indent=2`). Claude Code по умолчанию берёт ответ инструмента до 25 тыс.
#: токенов; 78 тыс. знаков не прошли. 30 тыс. — с запасом и для кириллицы.
AGENT_REPLY_MAX = 30_000

#: Служебные поля результата проверки и детали, без которых вывод тот же.
_HEAVY = ("depends_on", "inputs", "duration_ms", "evidence")
_PASSED = ("pass", "skip")


def reply_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _passed(item: dict) -> bool:
    if "status" in item:
        return item.get("status") in _PASSED
    return item.get("ok") is True


def _items(out: dict) -> list[dict]:
    """Результаты проверок и детали сборки — то, что множится с проектом."""
    found: list[dict] = []
    if isinstance(out.get("results"), list):
        found += [r for r in out["results"] if isinstance(r, dict)]
    if isinstance(out.get("parts"), dict):
        found += [p for p in out["parts"].values() if isinstance(p, dict)]
    if isinstance(out.get("worst"), dict):
        found.append(out["worst"])
    return found


def _strip(item: dict, keys: tuple[str, ...]) -> dict:
    kept = {k: v for k, v in item.items() if k not in keys}
    deps = item.get("depends_on")
    if "depends_on" in keys and isinstance(deps, (list, tuple)) and deps:
        kept["depends_on_n"] = len(deps)
    return kept


def fit_for_agent(result: Any, limit: int = AGENT_REPLY_MAX) -> Any:
    """Ответ, который влезает в `limit`, — или прежний, если и так влезал."""
    if not isinstance(result, dict) or reply_size(result) <= limit or not _items(result):
        return result
    out = dict(result)
    if isinstance(out.get("results"), list):
        out["results"] = list(out["results"])
    if isinstance(out.get("parts"), dict):
        out["parts"] = dict(out["parts"])
    full = reply_size(result)
    done: list[str] = []

    def each(fn) -> None:
        if isinstance(out.get("results"), list):
            out["results"] = [fn(r) if isinstance(r, dict) else r for r in out["results"]]
        if isinstance(out.get("parts"), dict):
            out["parts"] = {k: fn(v) if isinstance(v, dict) else v
                            for k, v in out["parts"].items()}
        if isinstance(out.get("worst"), dict):
            out["worst"] = fn(out["worst"])

    steps = [
        ("у прошедших — без зависимостей, входов, времени и доказательств",
         lambda: each(lambda r: _strip(r, _HEAVY) if _passed(r) else r)),
        ("зависимости — только числом (depends_on_n)",
         lambda: each(lambda r: _strip(r, ("depends_on",)))),
        ("прошедшие — только id, статус и запас",
         lambda: each(lambda r: {k: r[k] for k in ("id", "name", "status", "ok", "margin")
                                 if k in r} if _passed(r) else r)),
    ]

    def drop_passed() -> None:
        if isinstance(out.get("results"), list):
            kept = [r for r in out["results"] if not (isinstance(r, dict) and _passed(r))]
            out["results_omitted"] = len(out["results"]) - len(kept)
            out["results"] = kept
        if isinstance(out.get("parts"), dict):
            kept_parts = {k: v for k, v in out["parts"].items()
                          if not (isinstance(v, dict) and _passed(v))}
            out["parts_omitted"] = len(out["parts"]) - len(kept_parts)
            out["parts"] = kept_parts

    steps.append(("прошедших нет в списке — они строками в report", drop_passed))
    for what, step in steps:
        step()
        done.append(what)
        if reply_size(out) <= limit:
            break
    out["agent_note"] = (f"ответ сокращён для агента (было {full} знаков): "
                         + "; ".join(done) + ". Каждая строка — в report; полностью — "
                         "в окне или `datum <команда> --json`.")
    return out


# ------------------------------------------------------------ вид для агента
#: Поля, которые читает только окно: адреса `/obj/…` (агент берёт файл по
#: `path`), события для ленты, отпечаток файлов проекта.
_WINDOW_ONLY = ("events", "stamp")


def _no_urls(value: Any) -> Any:
    """Адрес `/obj/…` рядом с путём к файлу агенту не нужен — он читает файл."""
    if isinstance(value, dict):
        drop = "url" in value and ("path" in value or "digest" in value
                                   or "caption" in value)
        return {k: _no_urls(v) for k, v in value.items() if not (drop and k == "url")}
    if isinstance(value, list):
        return [_no_urls(v) for v in value]
    return value


def _built_part(item: dict, few: bool) -> dict:
    """Деталь из ответа сборки — без того, что нужно только окну.

    Габарит переезжает из `view` (остальное там — отпечаток и адрес GLB),
    положение остаётся, только когда деталь сдвинута или повёрнута. Список
    прочитанных параметров — когда деталей мало (спросили про эти): у сборки
    всего проекта он занимал девять десятых ответа (33 детали — 54 тыс. знаков
    при отчёте в 2.5 тыс.), и там остаётся его длина.
    """
    out = {k: v for k, v in item.items() if k not in ("view", "placement", "depends_on")}
    view = item.get("view")
    if isinstance(view, dict) and view.get("bbox") is not None:
        out["bbox"] = view["bbox"]
    where = item.get("placement")
    if isinstance(where, dict) and (any(where.get("move") or ()) or any(where.get("rotate") or ())):
        out["placement"] = where
    deps = item.get("depends_on")
    if isinstance(deps, (list, tuple)):
        if few or not item.get("ok", False):
            out["depends_on"] = list(deps)
        else:
            out["depends_on_n"] = len(deps)
    return out


def for_agent(name: str, result: Any, args: dict | None = None) -> Any:
    """Ответ операции в том виде, в каком он полезен агенту, — всегда, а не
    только когда не влез.

    Разбор сессий: ответ читается не один раз — он остаётся в контексте и
    перечитывается каждым следующим ходом. Поэтому из него уходит то, что
    агент не использует вовсе (поля окна), и то, что сказано дважды. Числа и
    выводы не трогаются: всё, по чему принимают решение, остаётся — в `report`
    построчно, а не прошедшее — ещё и полями.

      * `part_build` — у детали габарит вместо `view`; см. `_built_part`;
      * `check_run` без `only` — полями только НЕ прошедшее (отказы, ошибки,
        предупреждения, пропуски); прошедшие — строками в `report`; одну
        проверку целиком даёт `check_run(only=[id])`;
      * `check_list` — текст каталога без его же копии словарём.
    """
    if not isinstance(result, dict) or result.get("error"):
        return result
    args = args or {}
    out = {k: _no_urls(v) for k, v in result.items() if k not in _WINDOW_ONLY}
    notes: list[str] = []
    if name == "part_build" and isinstance(out.get("parts"), dict):
        few = len(out["parts"]) <= 3
        out["parts"] = {k: _built_part(v, few) if isinstance(v, dict) else v
                        for k, v in out["parts"].items()}
        if not few:
            notes.append("прочитанные параметры детали — числом (depends_on_n); "
                         "списком — part_build(parts=[деталь])")
    elif name == "check_run" and isinstance(out.get("results"), list) and not args.get("only"):
        kept = [{k: v for k, v in r.items() if k != "duration_ms"}
                for r in out["results"] if isinstance(r, dict) and not _passed(r)]
        omitted = len(out["results"]) - len(kept)
        out["results"] = kept
        if omitted:
            out["results_passed"] = omitted
            notes.append(f"прошедшие проверки ({omitted}) — строками в report; "
                         f"полями — check_run(only=[id])")
    elif name == "check_list" and "checks" in out:
        del out["checks"]
    if notes:
        out["agent_note"] = "; ".join(notes)
    return out
