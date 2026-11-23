"""Общее для всех операций: открытие проекта, отказ словами, типы аргументов.

Часть операций Datum (`tools/ops.py` — их общий вход: оттуда строятся
MCP-инструменты и команды CLI). Соглашение то же: операция возвращает
словарь, `report` — текст для человека, об ошибке — ключ `error`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Any, Literal

from datum.tools.registry import Arg, op


__all__ = ["open_project", "clear_project_cache"]

_PROJECTS: dict[str, Any] = {}


def open_project(path: str):
    """Открыть проект (с кешем в пределах процесса)."""
    from datum.doc.project import Project

    key = str(Path(path).resolve())
    if key not in _PROJECTS:
        _PROJECTS[key] = Project.open(key)
    return _PROJECTS[key]


def clear_project_cache() -> None:
    _PROJECTS.clear()


def project_root_only(project: str) -> Path:
    """Каталог проекта БЕЗ его открытия. `open_project` исполняет скрипты
    деталей, а операции, которые идут в процессе сервера (не изолированные),
    кода проекта исполнять не вправе (`docs/security.md`)."""
    root = Path(project).resolve()
    if not (root / "datum.toml").is_file():
        raise ValueError(f"{root} — не проект Datum: нет datum.toml")
    return root


def _err(message: str) -> dict:
    return {"error": message, "report": f"ОШИБКА: {message}"}


def _guard(fn):
    """Превратить исключение в результат с полем error."""
    try:
        return fn()
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")


Project = Annotated[str, Arg(help="путь к каталогу проекта", metavar="ПРОЕКТ")]

#: Наборы значений — в сигнатуре, а не в тексте подсказки: из неё их получают и
#: командная строка (choices), и схема MCP (перечисление). Агенту не приходится
#: угадывать, пишется `xz` или `XZ`, — раньше соседние операции ждали разного.
Plane = Literal["xy", "xz", "yz"]
Face = Literal["x_min", "x_max", "y_min", "y_max", "z_min", "z_max"]
Direction = Literal["+X", "-X", "+Y", "-Y", "+Z", "-Z"]


# ------------------------------------------------------------------ состояние
@op("datum_status", cli_name="status", group="система",
    title="Состояние Datum",
    summary="Версии ядра и библиотек, перечень доступных проверок.")
async def datum_status() -> dict[str, Any]:
    from importlib.metadata import PackageNotFoundError, version

    from datum.checks.library import LIBRARY
    from datum.tools.registry import OPS

    def of(pkg: str) -> str:
        # У manifold3d нет `__version__`, и раньше здесь печатался «?».
        try:
            return version(pkg)
        except PackageNotFoundError:
            return "не установлен"

    libs = {name: of(name) for name in
            ("datum", "manifold3d", "trimesh", "shapely", "numpy", "scikit-fem",
             "pytetwild")}
    py = sys.version.split()[0]
    return {**{k.replace("-", "_"): v for k, v in libs.items()}, "python": py,
            "operations": len(OPS),
            "checks_available": sorted(LIBRARY),
            "report": (f"Datum {libs['datum']}, Python {py}; "
                       + ", ".join(f"{k} {v}" for k, v in libs.items() if k != "datum")
                       + f"; операций {len(OPS)}, проверок в каталоге {len(LIBRARY)}")}
