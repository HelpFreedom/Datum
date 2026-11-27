import multiprocessing

import pytest

from datum.doc import checks_edit


def _append(root, ready, entered):
    ready.set()

    def append(text):
        entered.set()
        return text + "- id: second\n"

    checks_edit.update(root, append)


def test_two_processes_update_the_same_file(tmp_path):
    """Второй писатель ждёт, затем видит результат первого."""
    ctx = multiprocessing.get_context("spawn")
    ready, entered = ctx.Event(), ctx.Event()
    other = ctx.Process(target=_append, args=(tmp_path, ready, entered))

    def first(text):
        other.start()
        assert ready.wait(10)
        assert not entered.wait(0.3)
        return text + "- id: first\n"

    try:
        checks_edit.update(tmp_path, first)
        other.join(10)
        assert other.exitcode == 0
    finally:
        if other.is_alive():
            other.terminate()
            other.join(5)
    path = tmp_path / "checks" / "checks.yaml"
    assert path.read_text(encoding="utf-8") == "- id: first\n- id: second\n"


def test_failed_change_releases_lock(tmp_path):
    def fail(text):
        raise ValueError("отказ от правки")

    with pytest.raises(ValueError, match="отказ"):
        checks_edit.update(tmp_path, fail)
    assert checks_edit.update(tmp_path, lambda text: text + "- id: next\n") == "- id: next\n"


def test_existing_write_only_lock(tmp_path):
    lock = tmp_path / ".datum" / "checks.lock"
    lock.parent.mkdir()
    lock.touch(mode=0o200)
    assert checks_edit.update(tmp_path, lambda text: text + "- id: ok\n") == "- id: ok\n"
