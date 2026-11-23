"""Проект как вещь: переименовать (название и папку) и сделать новый из этого.

Откуда (аудит 2026-11-21, §7.8). Восемнадцать проектов назывались
`untitled-ГГГГММДД-ЧЧММСС` «Без названия», и найти нужный можно было только по
дате; руку v2 → v3 → v4 владелец вёл каталогами `archive_v3`, `archive_v4`.

Решение владельца (2026-11-22): переименование меняет И название, И папку.
Папку открытого проекта двигать нельзя — в ней работают сервер и агент;
поэтому операция отказывает, пока проект кем-то открыт, а окно («Файл →
Проекты…») сначала останавливает их, зовёт её и открывает проект заново.
Название без папки (`folder=false`) меняется и у открытого.

Проект операции не открывают: работа — с `datum.toml` и каталогом.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Annotated, Any

from datum.tools.ops import Project, _err, _guard, project_root_only
from datum.tools.registry import Arg, op

__all__ = ["project_rename", "project_copy", "folder_for", "set_title", "NAME_MAX"]

NAME_MAX = 80
#: Имя папки короче названия: путь проекта стоит в журналах, командах и ссылках.
FOLDER_MAX = 60
#: Что не переезжает в копию: кеш, корзина, запись об архиве и вердикт критика
#: (`.datum`), выгрузка (`output`) и байт-код.
COPY_SKIP = shutil.ignore_patterns(".datum", "output", "__pycache__", "*.pyc")


def _clean_name(name: str) -> str:
    text = " ".join(str(name).split())
    if not text:
        raise ValueError("название пустое")
    if len(text) > NAME_MAX:
        raise ValueError(f"название длиннее {NAME_MAX} знаков ({len(text)})")
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise ValueError("в названии управляющие знаки")
    return text


def folder_for(name: str) -> str:
    """Название → имя папки: пробелы — дефисом, недопустимое в путях убрано.
    Кириллица остаётся (папки «рука-v3», «пресс» у владельца так и названы)."""
    text = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]+', " ", _clean_name(name))
    text = re.sub(r"\s+", "-", text.strip()).strip(".-")[:FOLDER_MAX].rstrip(".-")
    if not text or text in (".", ".."):
        raise ValueError(f"из названия {name!r} не выходит имени папки — нужны буквы или цифры")
    return text


def set_title(root: Path, name: str) -> None:
    """Записать название в `datum.toml` — правкой строки `name = …` верхнего
    уровня (комментарии и порядок файла остаются), и перечитать: файл, который
    после правки не разбирается или говорит другое имя, на диск не попадает."""
    import tomllib

    from datum.doc.params_edit import write_atomically

    path = root / "datum.toml"
    text = path.read_text(encoding="utf-8")
    value = json.dumps(name, ensure_ascii=False)          # строка TOML в кавычках
    lines = text.split("\n")
    done = False
    for i, line in enumerate(lines):
        if re.match(r"\s*\[", line):
            break                                          # дальше — таблицы, не наше
        m = re.match(r"(\s*name\s*=\s*)(?:\"(?:[^\"\\]|\\.)*\"|'[^']*')(\s*(?:#.*)?)$", line)
        if m:
            lines[i] = f"{m.group(1)}{value}{m.group(2)}"
            done = True
            break
    new = "\n".join(lines) if done else f"name = {value}\n" + text
    try:
        got = tomllib.loads(new).get("name")
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"datum.toml после правки не разбирается ({e}) — не записан") from e
    if got != name:
        raise ValueError(f"datum.toml после правки называет проект {got!r}, а не {name!r} "
                         f"— не записан; поправьте строку name руками")
    write_atomically(path, new)


def _served(root: Path) -> list[int]:
    """pid живых серверов Datum, открытых на этом проекте."""
    from datum.server.handshake import announced

    try:
        return [h.pid for h in announced(root)]
    except Exception:                                # noqa: BLE001 — нет каталога объявлений
        return []


@op("project_rename", cli_name="rename", group="проект", danger="mutates",
    title="Переименовать проект",
    summary=("Дать проекту название (datum.toml: его показывают окно, список проектов и "
             "имя архива). folder=true — переименовать ещё и ПАПКУ проекта по названию: "
             "только у проекта, который никем не открыт (в открытом работают сервер и "
             "агент) — у открытого это делает окно, «Файл → Проекты…». Просят "
             "переименовать проект из разговора — меняйте название, а про папку скажите "
             "человеку, где кнопка."))
async def project_rename(
    project: Project,
    name: Annotated[str, Arg(help="новое название, до 80 знаков")],
    folder: Annotated[bool, Arg(help="переименовать и папку проекта")] = False,
) -> dict[str, Any]:
    def run():
        root = project_root_only(project).resolve()
        try:
            title = _clean_name(name)
            target = root.parent / folder_for(title) if folder else root
        except ValueError as e:
            return _err(str(e))
        moving = target != root
        if moving:
            served = _served(root)
            if served:
                return _err(f"проект открыт (сервер Datum, pid {', '.join(map(str, served))}): "
                            f"папку из-под работающего сервера и агента не двигают. В окне — "
                            f"«Файл → Проекты…» → переименовать (окно само остановит их и "
                            f"откроет проект заново); название без папки — folder=false")
            if target.exists() or target.is_symlink():
                return _err(f"папка {target} уже есть — выберите другое название или "
                            f"уберите её")
        try:
            set_title(root, title)
        except (OSError, ValueError) as e:
            return _err(str(e))
        if moving:
            try:
                os.rename(root, target)
            except OSError as e:
                return _err(f"название записано, а папку переименовать не вышло: {e}")
        lines = [f"проект называется «{title}»"]
        if moving:
            lines.append(f"папка: {root.name} → {target.name} ({target.parent})")
            lines.append("прежние сессии Claude Code привязаны к старому пути: разговор "
                         "в новой папке начнётся заново, а что было — в JOURNAL.md проекта")
        elif folder:
            lines.append(f"папка уже называется {root.name}")
        return {"name": title, "path": str(target), "moved": moving,
                "report": "\n".join(lines)}

    return _guard(run)


@op("project_copy", cli_name="copy", group="проект", danger="mutates",
    title="Новый проект из этого",
    summary=("Сделать новый проект копией этого — рядом, в папке по новому названию: "
             "исходники, параметры, проверки, импорт, версии, журнал; без кеша, корзины "
             "и выгрузки. Так начинают следующую версию изделия (v3 → v4), не теряя "
             "прежнюю. Этот проект не меняется; копию откройте в окне — «Файл → Проекты…»."))
async def project_copy(
    project: Project,
    name: Annotated[str, Arg(help="название нового проекта, до 80 знаков")],
) -> dict[str, Any]:
    def run():
        from datum.doc import journal

        root = project_root_only(project).resolve()
        try:
            title = _clean_name(name)
            target = root.parent / folder_for(title)
        except ValueError as e:
            return _err(str(e))
        if target.exists() or target.is_symlink():
            return _err(f"папка {target} уже есть — выберите другое название")
        import tomllib

        try:
            was = str(tomllib.loads((root / "datum.toml").read_text(encoding="utf-8"))
                      .get("name") or root.name)
        except (OSError, tomllib.TOMLDecodeError):
            was = root.name
        # Сначала во временную папку рядом: оборванная копия не должна выглядеть проектом.
        tmp = root.parent / f".{target.name}.copying"
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            shutil.copytree(root, tmp, symlinks=True, ignore=COPY_SKIP)
            set_title(tmp, title)
            try:
                journal.add(tmp, "этап", f"проект создан копией «{was}» ({root.name})")
            except journal.JournalError:
                pass                                  # журнал — справка, копия важнее
            os.rename(tmp, target)
        except (OSError, ValueError, shutil.Error) as e:
            shutil.rmtree(tmp, ignore_errors=True)
            return _err(f"копия не вышла: {e}")
        files = sum(1 for p in target.rglob("*") if p.is_file())
        return {"name": title, "path": str(target), "from": str(root),
                "report": (f"новый проект «{title}»: {target} — копия «{was}», файлов {files}; "
                           f"без кеша, корзины и выгрузки (детали соберутся при открытии)")}

    return _guard(run)
