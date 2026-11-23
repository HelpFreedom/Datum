"""Новый проект из шаблона — `datum init <каталог>`.

Проект создаётся ПУСТЫМ: устройство (манифест, параметры, проверки, parts/)
без единой детали (решение владельца, 2026-10-23). Прежний кубик-образец был
чужим в каждом новом проекте — его приходилось удалять, прежде чем начать
своё, а окно, запущенное без проекта, открывается именно таким.

Чужое не перезаписывается никогда: если в каталоге уже есть хоть что-то из
устройства проекта, это отказ, а не слияние.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

__all__ = ["init_project", "ScaffoldError"]

#: Что делает каталог проектом. Хоть одно из этого уже есть — отказ.
PROJECT_MARKS = ("datum.toml", "params", "parts", "checks", "assembly.yaml")


class ScaffoldError(RuntimeError):
    """Каталог нельзя превратить в проект, не испортив того, что в нём есть."""


def _toml_string(value: str) -> str:
    # json-строка — допустимая базовая строка TOML: те же экранирования.
    return json.dumps(value, ensure_ascii=False)


def _files(name: str) -> dict[str, str]:
    return {
        "datum.toml": f"""\
schema = 1
name = {_toml_string(name)}
units = "mm"

# Принтер. Проверки печатности берут стол и слой отсюда.
[printer]
profile = "elegoo-cc2-0.4"
bed = [256, 256, 256]
nozzle = 0.4
layer = 0.20
material = "PLA"

[build]
guard = "checkpoint"
""",
        "params/base.yaml": """\
# Параметры проекта. У каждого числа — происхождение:
#   measured — намерено (с source: чем и как), assumed — угадано (с range),
#   derived — выведено формулой (expr), standard — из стандарта или паспорта,
#   chosen — решение конструктора (с reason).
# Правка из окна или операцией `datum set` сохраняет комментарии.

# Угаданное объявляют так — и тогда `datum sweep` покажет, решает ли оно дело:
# HOLE_D:
#   value: 3.4
#   unit: mm
#   provenance: assumed
#   range: [3.2, 3.6]
#   reason: "под винт M3, посадка не проверена на этом принтере"
#   blocking: true
""",
        "checks/checks.yaml": """\
# Проверки из каталога (`datum checks` — весь каталог с допущениями).
# Ссылки: "@деталь", "${params.ИМЯ}", "${expr: формула}" — в кавычках.
""",
        "NOTES.md": f"""\
# {name}

Здесь живёт рассуждение: почему выбрано так, что померено, что не сработало
на печати. Числа — в params/base.yaml, проверки — в checks/checks.yaml.
""",
        ".gitignore": ".datum/\noutput/\n",
        # Чтобы `claude`, запущенный в каталоге проекта, видел инструменты Datum.
        # Путь к интерпретатору — тот, которым выполнен `datum init`.
        ".mcp.json": json.dumps(
            {"mcpServers": {"datum": {"command": sys.executable,
                                      "args": ["-m", "datum.cli", "mcp", "--stdio"]}}},
            ensure_ascii=False, indent=2) + "\n",
    }


def init_project(root: str | Path, name: str = "") -> list[Path]:
    """Создать пустой проект в `root`. Возвращает созданные файлы.

    Каталог может существовать (например, пустой репозиторий), но в нём не
    должно быть ничего из устройства проекта.
    """
    root = Path(root).expanduser().resolve()
    if root.exists() and not root.is_dir():
        raise ScaffoldError(f"{root} — файл, а не каталог")
    taken = [m for m in PROJECT_MARKS if (root / m).exists()]
    if taken:
        raise ScaffoldError(
            f"{root} уже похож на проект: есть {', '.join(taken)}. Шаблон ничего "
            f"не перезаписывает — выберите пустой каталог")
    name = (name or root.name).strip()
    if not name or re.search(r"[\x00-\x1f]", name):
        raise ScaffoldError(f"негодное имя проекта: {name!r}")

    files = _files(name)
    # Не затираем и то, что вне устройства проекта (свой NOTES.md, .gitignore).
    kept = {rel for rel in files if (root / rel).exists()}
    made = []
    for rel, text in files.items():
        if rel in kept:
            continue
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        made.append(path)
    (root / "parts").mkdir(parents=True, exist_ok=True)
    return made
