"""Перила для скриптов проекта — импорт и запись.

ЭТО НЕ ГРАНИЦА БЕЗОПАСНОСТИ, и так и задумано. Скрипт детали — произвольный
Python в том же процессе; обойти любую подмену внутри процесса он может в одну
строку. Настоящая граница проходит по другому месту: отдельный процесс, пределы
ресурсов, запрет сети, петлевая привязка сервера.

Тогда зачем? Затем, что подавляющее большинство бед здесь — не злой умысел, а
промах. Скрипт, написанный агентом, пытается сделать `os.makedirs("output")`
или `sys.path.insert(...)` по привычке из старого рабочего процесса; ошибка в
пути кладёт файл в домашний каталог; перенесённый скрипт пишет прямо в
каталог исходного проекта, который трогать нельзя. Перила ловят это НЕМЕДЛЕННО и
объясняют, чем пользоваться вместо — вместо того, чтобы дать промаху молча
осуществиться.

Два правила.

1. ИМПОРТ. Скриптам ПРОЕКТА (`parts/`, `lib/`, `checks/`) доступен ограниченный
   набор модулей. Ограничение касается только их: библиотеки импортируют что
   угодно, иначе не взлетит ни numpy, ни trimesh. Кто импортирует — видно из
   пространства имён, переданного в `__import__`.

2. ЗАПИСЬ. Писать можно в проект и во временные каталоги. Всё остальное —
   отказ с указанием пути. Чтение не ограничивается никак: читать чужую модель
   или справочник — обычное дело, а угроза здесь не в чтении.

Оба правила видят только то, что идёт через Python. Библиотека, открывающая
файл своими средствами в C (так делает sqlite3), проходит мимо — и это ещё
одна причина не считать происходящее здесь границей безопасности.
"""

from __future__ import annotations

import builtins
import io
import os
import tempfile
from pathlib import Path

__all__ = ["ImportDenied", "WriteDenied", "install", "ALLOWED_IMPORTS",
           "DENIED_IMPORTS"]


class ImportDenied(ImportError):
    """Скрипт проекта импортирует модуль, которого ему не положено."""


class WriteDenied(PermissionError):
    """Попытка записи за пределы проекта и временных каталогов."""


#: Что скрипту детали действительно нужно. Список выведен из настоящих скриптов
#: рабочего процесса CAD, а не придуман: там на 175 обращений к проектному `lib`
#: приходится 153 к shapely, 110 к trimesh, 97 к math и 43 к numpy.
ALLOWED_IMPORTS = frozenset({
    # своё: Datum и помощники проекта из lib/ (см. build.context._ProjectLibFinder)
    "datum", "lib",
    # геометрия и счёт
    "manifold3d", "trimesh", "shapely", "numpy", "scipy", "build123d",
    "lib3mf", "networkx", "PIL",
    # математика
    "math", "cmath", "statistics", "random", "fractions", "decimal", "numbers",
    # обычный Python
    "typing", "dataclasses", "functools", "itertools", "operator", "collections",
    "enum", "abc", "copy", "re", "string", "textwrap", "bisect", "heapq",
    "json", "yaml", "tomllib", "csv", "struct", "array", "base64", "hashlib",
    "time", "datetime", "warnings", "contextlib", "traceback", "logging",
    # файлы: читать — можно, писать не туда не даст второе правило
    "pathlib", "io", "zipfile", "tempfile", "shutil", "glob", "fnmatch",
})

#: Запрещённое поимённо — с объяснением, чем пользоваться вместо. Пустая строка
#: означала бы «нельзя, и разбирайся сам», а это худший вид отказа.
DENIED_IMPORTS: dict[str, str] = {
    "os": "путями и каталогами ведает Datum: выход для слайсера пишет операция export, "
          "а временные файлы — в tempfile. Для чтения файла хватает pathlib",
    "sys": "деталь не правит sys.path и не завершает процесс: её модули "
           "подхватываются из parts/ и lib/ сами, а результат возвращается "
           "оператором return",
    "subprocess": "вызов внешних программ идёт через операции Datum, а не из "
                  "скрипта детали: иначе результат нельзя ни закешировать, ни "
                  "повторить",
    "ctypes": "прямой доступ к памяти процесса обходит все пределы разом",
    "socket": "сети у скрипта детали нет. Нужны внешние данные — положите их в "
              "data/ проекта и читайте из файла: каталог data/ входит в ключ кеша "
              "сборки, и правка файла пересоберёт деталь",
    "urllib": "сети у скрипта детали нет. Нужны внешние данные — положите их в "
              "data/ проекта и читайте из файла: каталог data/ входит в ключ кеша "
              "сборки, и правка файла пересоберёт деталь",
    "requests": "сети у скрипта детали нет. Нужны внешние данные — положите их "
                "в data/ проекта и читайте из файла: каталог data/ входит в ключ "
                "кеша сборки, и правка файла пересоберёт деталь",
    "http": "сети у скрипта детали нет",
    "importlib": "динамический импорт обходит этот самый список",
    "multiprocessing": "параллелизмом ведает Datum: он и так гоняет операции по "
                       "процессам и умеет их отменять",
    "threading": "потоки в скрипте детали делают сборку невоспроизводимой",
    "signal": "сигналами управляет Datum: на них держится отмена",
    "resource": "пределы процесса выставлены снаружи и менять их изнутри нельзя",
    "gc": "вмешательство в сборку мусора ломает учёт памяти",
    "pty": "запуск программ из скрипта детали запрещён",
}

_IMPORT_MACHINERY = ("importlib", "_frozen_importlib", "_frozen_importlib_external")


def _under(path: str, roots: tuple[str, ...]) -> bool:
    try:
        real = os.path.realpath(path)
    except (OSError, ValueError):                 # pragma: no cover
        return False
    return any(real == r or real.startswith(r + os.sep) for r in roots)


# --------------------------------------------------------------- правило 1
def _install_import_guard(roots: tuple[str, ...], allowed: frozenset[str]) -> None:
    """Перехват `builtins.__import__`.

    Первая версия ставила искатель в `sys.meta_path` — и не ловила НИЧЕГО из
    того, ради чего задумывалась. Искателей спрашивают только о модулях,
    которых ещё нет в `sys.modules`, а `os`, `sys` и `subprocess` загружены
    задолго до запуска скрипта: `import os` просто берёт готовый модуль, и до
    цепочки искателей дело не доходит. `__import__` же зовётся на КАЖДЫЙ
    оператор импорта, в том числе на повторный.

    Кто импортирует — видно прямо из аргумента `globals`: это пространство имён
    импортирующего модуля, а в нём `__file__`. Это и надёжнее обхода кадров
    стека, и дешевле.
    """
    real_import = builtins.__import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0:                     # относительный импорт — внутри проекта
            top = str(name).partition(".")[0]
            if top and top not in allowed:
                source = (globals or {}).get("__file__")
                if source and _under(source, roots):
                    why = DENIED_IMPORTS.get(top)
                    detail = f": {why}" if why else (
                        ". Разрешены: " + ", ".join(sorted(allowed)))
                    raise ImportDenied(
                        f"скрипту проекта запрещён импорт «{top}»{detail}",
                        name=name)
        return real_import(name, globals, locals, fromlist, level)

    builtins.__import__ = guarded          # type: ignore[assignment]


# --------------------------------------------------------------- правило 2
_WRITE_FLAGS = frozenset("wax+")


def _install_write_guard(roots: tuple[str, ...]) -> None:
    real_open = builtins.open
    real_os_open = os.open
    allowed_text = ", ".join(roots)

    def refuse(path) -> "WriteDenied":
        return WriteDenied(
            f"запись за пределы проекта запрещена: {path}. "
            f"Писать можно в {allowed_text}")

    def guarded_open(file, mode="r", *a, **k):
        if isinstance(file, (str, bytes, os.PathLike)) and (
                _WRITE_FLAGS & set(str(mode))) and not _under(os.fspath(file), roots):
            raise refuse(os.fspath(file))
        return real_open(file, mode, *a, **k)

    wr = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC

    def guarded_os_open(path, flags, *a, **k):
        if (flags & wr) and isinstance(path, (str, bytes, os.PathLike)) \
                and not _under(os.fspath(path), roots):
            raise refuse(os.fspath(path))
        return real_os_open(path, flags, *a, **k)

    builtins.open = guarded_open            # type: ignore[assignment]
    os.open = guarded_os_open               # type: ignore[assignment]
    # `io.open` — ОТДЕЛЬНАЯ ссылка на тот же объект, и подмена `builtins.open`
    # её не трогает. Через неё ходит весь pathlib, то есть самый обычный способ
    # записать файл: `Path(...).write_text(...)`. Без этой строки перила
    # пропускали ровно тот случай, ради которого ставились.
    io.open = guarded_open                  # type: ignore[assignment]

    # Удаление и переименование — та же беда с другой стороны. Обёртка общая:
    # проверяется КАЖДЫЙ путевой аргумент, поэтому os.replace(чужое, своё)
    # отвергается так же, как os.replace(своё, чужое).
    for name in ("remove", "unlink", "rmdir", "removedirs", "mkdir", "makedirs",
                 "rename", "replace", "renames", "truncate", "chmod", "symlink",
                 "link"):
        original = getattr(os, name, None)
        if original is None:                # pragma: no cover
            continue

        def wrap(original=original):
            def guarded(*a, **k):
                for value in a:
                    if isinstance(value, (str, bytes, os.PathLike)) \
                            and not _under(os.fspath(value), roots):
                        raise refuse(os.fspath(value))
                return original(*a, **k)
            return guarded

        setattr(os, name, wrap())


# ------------------------------------------------------------------- вместе
def install(project: str | Path, *, extra_imports: tuple[str, ...] = (),
            extra_write_roots: tuple[str, ...] = ()) -> None:
    """Поставить перила. Вызывается в ДОЧЕРНЕМ процессе, до работы скрипта."""
    project_real = os.path.realpath(str(project))

    # Каталоги проекта, откуда берутся скрипты. Правило импорта касается только
    # их: всё остальное — библиотеки, и ограничивать их бессмысленно.
    script_roots = tuple(
        os.path.realpath(os.path.join(project_real, sub))
        for sub in ("parts", "lib", "checks"))

    write_roots = tuple(dict.fromkeys(
        [project_real, os.path.realpath(tempfile.gettempdir())]
        + [os.path.realpath(r) for r in extra_write_roots]))

    _install_import_guard(script_roots,
                          ALLOWED_IMPORTS | frozenset(extra_imports))
    _install_write_guard(write_roots)
