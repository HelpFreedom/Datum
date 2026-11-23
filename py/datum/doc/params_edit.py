"""Точечная правка params/base.yaml — без потери комментариев.

`ParamRegistry.save` переписывает файл целиком, а комментарии в реестре
параметров — не украшение: там записано, почему число такое, откуда взято и
что с ним уже случалось. Правка из окна не должна их стирать. Поэтому файл
правится как ТЕКСТ, по месту: меняется одно поле одного параметра, остальное
остаётся байт в байт.

Понимаются обе формы, что встречаются в проектах:

    GAP:     {value: 0.30, unit: mm, provenance: chosen,
              reason: "скользящая посадка"}          ← поток, в том числе на строки

    E_PLA:
      value: 2000.0                                   ← блок
      unit: MPa

Правка проверяется до записи: исправленный текст разбирается целиком, и
параметр обязан прочитаться с новым значением. Запись атомарная — временный
файл и переименование, — поэтому редактор или агент, читающий файл в этот
момент, не увидит его наполовину.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

__all__ = ["set_fields", "add_param", "param_text", "write_atomically", "EditError"]


class EditError(ValueError):
    """Правку не удалось сделать по месту."""


def _key_line(text: str, name: str) -> re.Match:
    m = re.search(rf"^{re.escape(name)}[ \t]*:", text, re.M)
    if m is None:
        raise EditError(f"параметра {name!r} нет в файле")
    return m


def _flow_span(text: str, start: int) -> tuple[int, int] | None:
    """Границы «{…}» потокового словаря, начинающегося после двоеточия ключа."""
    i = start
    while i < len(text) and text[i] in " \t":
        i += 1
    if i >= len(text) or text[i] != "{":
        return None
    depth, quote = 0, ""
    for j in range(i, len(text)):
        ch = text[j]
        if quote:
            if ch == "\\":
                continue
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i, j
    raise EditError("не закрыта фигурная скобка в описании параметра")


def _block_span(text: str, after: int) -> tuple[int, int]:
    """Строки блока параметра: до следующей строки, начинающейся не с пробела."""
    end = text.find("\n", after)
    if end < 0:
        return after, len(text)
    m = re.search(r"^(?![ \t]|\n|#)", text[end + 1:], re.M)
    stop = end + 1 + m.start() if m else len(text)
    return end + 1, stop


def param_text(text: str, name: str) -> str:
    """Описание параметра ТЕКСТОМ, как оно стоит в файле: строка ключа и всё,
    что к ней относится, — с комментариями внутри. Так параметр переезжает в
    библиотеку компонентов и обратно, не теряя рассуждения."""
    m = _key_line(text, name)
    flow = _flow_span(text, m.end())
    if flow is not None:
        end = text.find("\n", flow[1])
        return text[m.start():len(text) if end < 0 else end + 1]
    _, stop = _block_span(text, m.end())
    # Хвост блока — пустые строки и комментарии СЛЕДУЮЩЕГО параметра: не наши.
    lines = text[m.start():stop].splitlines(keepends=True)
    while len(lines) > 1 and (not lines[-1].strip() or lines[-1].lstrip().startswith("#")
                              and not lines[-1][:1].isspace()):
        lines.pop()
    return "".join(lines)


#: Поля, значение которых — слово из словаря, а не свободный текст: их
#: пишем без кавычек, как в остальном файле.
_WORDS = {"provenance", "unit"}


def _render(value, field: str = "") -> str:
    if field in _WORDS and isinstance(value, str) and re.fullmatch(r"[A-Za-zА-Яа-я]+", value):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        # Диапазон догадки: [низ, верх] — потоковым списком, как пишут руками.
        return "[" + ", ".join(_render(v) for v in value) + "]"
    if isinstance(value, (int, float)):
        return repr(float(value)) if float(value) != int(value) else f"{float(value):.1f}"
    return json.dumps(str(value), ensure_ascii=False)


def _drop(text: str, name: str, field: str) -> str:
    """Убрать поле у параметра, если оно есть."""
    key = _key_line(text, name)
    flow = _flow_span(text, key.end())
    if flow is not None:
        lo, hi = flow
        body = text[lo:hi + 1]
        body = re.sub(rf",?[ \t]*\n?[ \t]*\b{re.escape(field)}[ \t]*:[ \t]*"
                      rf"(\"(?:[^\"\\]|\\.)*\"|'[^']*'|\[[^\]]*\]|[^,}}\n]+?)(?=[ \t]*[,}}\n])",
                      "", body, count=1)
        body = body.replace("{, ", "{")
        return text[:lo] + body + text[hi + 1:]
    start, stop = _block_span(text, key.end())
    block = text[start:stop]
    indent = re.match(r"[ \t]+", block)
    pad = re.escape(indent.group(0) if indent else "  ")
    # Поле-список (`range:\n    - 0.3\n    - 1.5`) уходит вместе с вложенными строками.
    block = re.sub(rf"^{pad}{re.escape(field)}[ \t]*:.*\n?(?:{pad}[ \t]+.*\n?)*", "", block,
                   count=1, flags=re.M)
    return text[:start] + block + text[stop:]


def set_fields(text: str, name: str, fields: dict, remove: tuple[str, ...] = ()) -> str:
    """Вернуть текст, где у параметра `name` поля `fields` заменены или
    добавлены, а поля `remove` убраны."""
    for field in remove:
        text = _drop(text, name, field)
    key = _key_line(text, name)
    flow = _flow_span(text, key.end())
    for field, value in fields.items():
        rendered = _render(value, field)
        if flow is not None:
            lo, hi = flow
            body = text[lo:hi + 1]
            pattern = re.compile(rf"(\b{re.escape(field)}[ \t]*:[ \t]*)"
                                 rf"(\"(?:[^\"\\]|\\.)*\"|'[^']*'|[^,}}\n]+?)(?=[ \t]*[,}}\n])")
            if pattern.search(body):
                body = pattern.sub(lambda m: m.group(1) + rendered, body, count=1)
            else:
                body = body[:-1].rstrip() + f", {field}: {rendered}}}"
            text = text[:lo] + body + text[hi + 1:]
            flow = _flow_span(text, _key_line(text, name).end())
        else:
            start, stop = _block_span(text, key.end())
            block = text[start:stop]
            # Только на отступе самого параметра: у вложенного списка
            # расхождений (`conflicts:` → `- value: …, source: …`) те же имена
            # полей, и замена не должна туда попасть.
            indent = re.match(r"[ \t]+", block)
            pad = indent.group(0) if indent else "  "
            pattern = re.compile(rf"^({re.escape(pad)}{re.escape(field)}[ \t]*:[ \t]*)(.*?)[ \t]*$",
                                 re.M)
            if pattern.search(block):
                block = pattern.sub(lambda m: m.group(1) + rendered, block, count=1)
            else:
                lines = block.rstrip("\n").split("\n") if block.strip() else []
                # Вставляем после последней строки самого параметра, а не после
                # пустых строк и комментариев, что идут уже к следующему.
                last = max((i for i, line in enumerate(lines)
                            if line.startswith(pad) and not line.strip().startswith("#")),
                           default=-1)
                lines.insert(last + 1, f"{pad}{field}: {rendered}")
                tail = block[len(block.rstrip("\n")):] or "\n"
                block = "\n".join(lines) + tail
            text = text[:start] + block + text[stop:]
            key = _key_line(text, name)
    return text


#: Порядок полей новой записи — как их пишут руками в проектах.
_ORDER = ("value", "expr", "unit", "provenance", "range", "source", "ref", "reason",
          "measure_hint", "note", "blocking")


def add_param(text: str, name: str, body: dict, after: str = "") -> str:
    """Вернуть текст с новым параметром `name` — одной строкой-словарём.

    `after` — имя параметра, за которым поставить (в его разделе файла);
    пусто — в конец. Существующий параметр здесь не правится — отказ. Разбор
    результата реестром — дело вызывающего: здесь только текст.
    """
    if re.search(rf"^{re.escape(name)}[ \t]*:", text, re.M):
        raise EditError(f"параметр {name!r} уже есть в файле")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise EditError(f"имя параметра {name!r}: нужен идентификатор — латинские буквы, "
                        f"цифры и подчёркивание")
    unknown = [k for k in body if k not in _ORDER]
    if unknown:
        raise EditError(f"неизвестные поля параметра: {unknown}")
    parts = []
    for key in _ORDER:
        if key not in body:
            continue
        value = body[key]
        if key == "unit" and value == "":
            rendered = '""'
        else:
            rendered = _render(value, key)
        parts.append(f"{key}: {rendered}")
    line = f"{name}: {{{', '.join(parts)}}}\n"
    if not after:
        return (text if text.endswith("\n") or not text else text + "\n") + line
    key = _key_line(text, after)
    flow = _flow_span(text, key.end())
    if flow is not None:
        end = text.find("\n", flow[1])
        stop = len(text) if end < 0 else end + 1
    else:
        stop = _block_span(text, key.end())[1]
    head = text[:stop]
    # Пустые строки и комментарии перед следующим параметром относятся к нему,
    # а не к `after`: новую запись ставим до них.
    kept = head.rstrip("\n")
    lines = kept.split("\n")
    while lines and (not lines[-1].strip() or lines[-1].lstrip().startswith("#")) \
            and not lines[-1].startswith((" ", "\t")):
        lines.pop()
    cut = len("\n".join(lines))
    return text[:cut] + "\n" + line + _rest(text[cut:])


def _rest(tail: str) -> str:
    """Хвост файла после вставленной записи: одна пустая строка-разделитель
    сохраняется, если она там была."""
    stripped = tail.lstrip("\n")
    gap = len(tail) - len(stripped)
    return ("\n" * max(gap - 1, 0)) + stripped


def write_atomically(path: Path, text: str) -> None:
    """Записать файл целиком: временный рядом, затем переименование."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        try:
            os.chmod(tmp, path.stat().st_mode & 0o777)
        except OSError:                           # pragma: no cover
            pass
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
