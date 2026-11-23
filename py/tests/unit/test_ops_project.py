"""Проект как вещь: переименовать (название и папку) и сделать новый из этого."""

from __future__ import annotations

import asyncio
import tomllib

import pytest

from datum.doc import journal
from datum.tools import ops, ops_project


@pytest.fixture(autouse=True)
def fresh():
    ops.clear_project_cache()
    yield
    ops.clear_project_cache()


def _run(coro):
    return asyncio.run(coro)


def _name(root) -> str:
    return tomllib.loads((root / "datum.toml").read_text("utf-8"))["name"]


# ------------------------------------------------------------------ название
def test_the_title_is_changed_in_place_and_the_file_keeps_its_comments(latch):
    before = (latch / "datum.toml").read_text("utf-8")
    res = _run(ops_project.project_rename(str(latch), '  Коробка  "с защёлкой" \\ v2 '))
    assert res["name"] == 'Коробка "с защёлкой" \\ v2' and res["moved"] is False
    assert _name(latch) == res["name"]
    after = (latch / "datum.toml").read_text("utf-8")
    assert after.count("\n") == before.count("\n"), "правится одна строка"
    assert [l for l in before.splitlines() if l.startswith("#")] == \
        [l for l in after.splitlines() if l.startswith("#")]
    # Проект с новым названием открывается и собирается как прежде.
    assert _run(ops.project_info(str(latch)))["name"] == res["name"]


def test_a_manifest_without_a_name_gets_one_and_a_table_name_is_not_touched(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "datum.toml").write_text('schema = 1\n\n[printer]\nname = "принтер"\n', "utf-8")
    ops_project.set_title(root, "Новый")
    data = tomllib.loads((root / "datum.toml").read_text("utf-8"))
    assert data["name"] == "Новый" and data["printer"]["name"] == "принтер"


@pytest.mark.parametrize("name, words", [
    ("   ", "пустое"), ("я" * 81, "длиннее"), ("а\x07б", "управляющие"),
])
def test_a_name_that_is_not_a_name_is_refused(latch, name, words):
    before = (latch / "datum.toml").read_text("utf-8")
    assert words in _run(ops_project.project_rename(str(latch), name))["error"]
    assert (latch / "datum.toml").read_text("utf-8") == before


@pytest.mark.parametrize("name, folder", [
    ("Gear Cube", "Gear-Cube"), ("  рука   v3 ", "рука-v3"), ("a/b\\c: d?", "a-b-c-d"),
    ("...скрытый.", "скрытый"), ("x" * 80, "x" * 60),
])
def test_the_folder_name_comes_from_the_title(name, folder):
    assert ops_project.folder_for(name) == folder


def test_a_title_of_punctuation_gives_no_folder():
    with pytest.raises(ValueError, match="не выходит имени папки"):
        ops_project.folder_for("/// ...")


# --------------------------------------------------------------------- папка
def test_the_folder_follows_the_title_when_asked(latch):
    res = _run(ops_project.project_rename(str(latch), "Коробка v2", True))
    moved = latch.parent / "Коробка-v2"
    assert res["moved"] is True and res["path"] == str(moved.resolve())
    assert not latch.exists() and _name(moved) == "Коробка v2"
    assert (moved / "parts").is_dir()
    assert "прежние сессии Claude Code" in res["report"]
    # Тем же названием второй раз — папка уже та: ничего не двигается.
    again = _run(ops_project.project_rename(str(moved), "Коробка v2", True))
    assert again["moved"] is False and "уже называется" in again["report"]


def test_an_existing_folder_is_never_overwritten(latch):
    (latch.parent / "занято").mkdir()
    before = (latch / "datum.toml").read_text("utf-8")
    res = _run(ops_project.project_rename(str(latch), "занято", True))
    assert "уже есть" in res["error"] and latch.is_dir()
    assert (latch / "datum.toml").read_text("utf-8") == before, "и название не тронуто"


def test_the_folder_of_a_project_someone_has_open_is_not_moved(latch, monkeypatch, tmp_path):
    """В открытом проекте работают сервер и агент: папку двигает окно, остановив их."""
    from datum.server import handshake

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    (tmp_path / "run").mkdir(mode=0o700)
    said = handshake.announce(port=1, token="t" * 32, project=str(latch))
    try:
        res = _run(ops_project.project_rename(str(latch), "Другое", True))
        assert "проект открыт" in res["error"] and "Файл → Проекты" in res["error"]
        assert latch.is_dir() and _name(latch) == "latch"
        # Название без папки меняется и у открытого.
        assert _run(ops_project.project_rename(str(latch), "Другое"))["name"] == "Другое"
    finally:
        handshake.withdraw(said)


# --------------------------------------------------------------------- копия
def test_a_new_project_from_this_one_carries_the_sources_not_the_cache(latch):
    _run(ops.part_build(str(latch)))                       # появился .datum/cache
    (latch / "output").mkdir(exist_ok=True)
    (latch / "output" / "x.3mf").write_text("x")
    ops.clear_project_cache()
    res = _run(ops_project.project_copy(str(latch), "Коробка v2"))
    copy = latch.parent / "Коробка-v2"
    assert res["path"] == str(copy.resolve()) and _name(copy) == "Коробка v2"
    assert _name(latch) == "latch", "исходный проект не меняется"
    assert not (copy / ".datum").exists() and not (copy / "output").exists()
    assert sorted(p.name for p in (copy / "parts").iterdir() if p.suffix == ".py") == \
        sorted(p.name for p in (latch / "parts").iterdir() if p.suffix == ".py")
    assert "создан копией «latch»" in journal.read(copy)[-1].text
    assert journal.read(latch) == []
    ops.clear_project_cache()
    assert _run(ops.part_build(str(copy)))["ok"], "копия собирается сама"


def test_a_copy_onto_an_existing_folder_is_refused_and_leaves_nothing(latch):
    (latch.parent / "v2").mkdir()
    res = _run(ops_project.project_copy(str(latch), "v2"))
    assert "уже есть" in res["error"]
    assert sorted(p.name for p in latch.parent.iterdir()) == ["latch", "v2"]
