"""Слои пакета держит тест, а не намерение.

`geom` — единственный, кто импортирует manifold3d: остальной Datum говорит с
ядром через `Solid` и функции `datum.geom`. Правило было записано (CLAUDE.md),
а `build/cache.py` его нарушал — сам собирал `Manifold(Mesh(...))`, и ничто
этого не ловило. Идея проверять слои кодом — из cadcraft (`cargo xtask layers`).
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "datum"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def test_only_geom_imports_the_kernel():
    offenders = sorted(
        str(path.relative_to(PACKAGE.parent))
        for path in PACKAGE.rglob("*.py")
        if "manifold3d" in _imports(path) and PACKAGE / "geom" not in path.parents)
    assert not offenders, f"manifold3d импортирует не geom: {offenders}"


def test_the_scan_sees_imports_inside_functions():
    """Ядро в Datum импортируют лениво, внутри функций, — скан обязан это видеть."""
    assert "manifold3d" in _imports(PACKAGE / "geom" / "convert.py")
    assert "manifold3d" in _imports(PACKAGE / "geom" / "footprint.py")


def test_operations_in_the_server_process_never_run_project_code():
    """Открытие проекта исполняет скрипты деталей (`Project.open` грузит
    модули `parts/`). Неизолированная операция идёт в процессе сервера, и
    там кода проекта быть не должно (`docs/security.md`, «Отдельный процесс с
    пределами»). `fonts_list`, `font_add` и `critic_verdict` открывали проект
    ради одного пути к нему — теперь `project_root_only`. Правило — для всех."""
    import inspect

    import datum.tools.ops  # noqa: F401
    from datum.tools.registry import OPS

    opening = ("open_project(", "Project.open(", ".build(", "run_checks(")
    offenders = sorted(
        spec.name for spec in OPS.values()
        if not spec.isolate
        and any(call in inspect.getsource(inspect.unwrap(spec.fn)) for call in opening))
    assert not offenders, f"неизолированные операции исполняют код проекта: {offenders}"
