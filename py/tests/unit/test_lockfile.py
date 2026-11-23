"""Фиксация зависимостей не должна отставать от объявленных.

`requirements.lock` собирается вручную и потому обязательно однажды отстанет:
кто-то добавит зависимость в `pyproject.toml` и забудет пересобрать замок.
Тогда `pip install --require-hashes` на чистой машине поставит НЕ ТО, что
проверено, — а узнается это в самый неудобный момент.
"""

from __future__ import annotations

import pathlib
import re
import tomllib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
LOCK = ROOT / "requirements.lock"

#: Наборы, которые входят в замок. `brep` не входит нарочно: это 2 ГБ OCCT,
#: и он опционален — см. docs/security.md.
LOCKED_EXTRAS = ("server", "mcp", "mesh", "sim", "dev")


def _name(requirement: str) -> str:
    """Имя пакета из строки требования, приведённое к сравнимому виду."""
    bare = re.split(r"[<>=!~\[;]", requirement, maxsplit=1)[0]
    return bare.strip().lower().replace("_", "-")


@pytest.fixture(scope="module")
def declared() -> set[str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    names = {_name(r) for r in data["project"]["dependencies"]}
    extras = data["project"].get("optional-dependencies", {})
    for extra in LOCKED_EXTRAS:
        names |= {_name(r) for r in extras.get(extra, [])}
    return names


@pytest.fixture(scope="module")
def locked() -> dict[str, str]:
    text = LOCK.read_text(encoding="utf-8")
    found = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s\\]+)", line)
        if m:
            found[m.group(1).lower().replace("_", "-")] = m.group(2)
    return found


def test_the_lock_exists_and_carries_hashes(locked):
    assert LOCK.is_file(), f"нет файла фиксации {LOCK}"
    text = LOCK.read_text(encoding="utf-8")
    assert "--hash=sha256:" in text, "фиксация без хешей не защищает ни от чего"
    assert len(locked) > 30, f"в замке подозрительно мало пакетов: {len(locked)}"


def test_every_declared_dependency_is_locked(declared, locked):
    missing = sorted(declared - set(locked))
    assert not missing, (
        f"объявлены, но не зафиксированы: {missing}. Пересоберите замок: "
        f"pip-compile --generate-hashes --strip-extras "
        f"--extra server --extra mcp --extra mesh --extra dev "
        f"-o requirements.lock pyproject.toml")


def test_vtk_did_not_sneak_in(locked):
    """`pytetwild` объявляет pyvista в наборе `all`, а тот тянет VTK на 639 МБ.
    В основных зависимостях он требует только numpy — но проверить стоит, а не
    надеяться: набор могут однажды поменять."""
    for unwanted in ("pyvista", "vtk"):
        assert unwanted not in locked, (
            f"{unwanted} попал в замок — это 639 МБ, от которых проект отказался")


def test_every_locked_version_matches_what_is_installed(locked):
    """Замок обязан описывать ТУ СРЕДУ, на которой всё проверено.

    Разойдись он с установленным — и `--require-hashes` на чистой машине
    поставит не то, что прогоняли тесты.
    """
    from importlib import metadata

    drift = []
    for name, version in sorted(locked.items()):
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue                    # набор dev может быть поставлен не весь
        if installed != version:
            drift.append(f"{name}: замок {version}, установлено {installed}")
    assert not drift, "замок разошёлся со средой:\n  " + "\n  ".join(drift)
