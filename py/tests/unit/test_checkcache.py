"""Кеш результатов проверок: быстрый — и никогда не устаревший.

Устаревший ответ здесь — это ложное «прошло», поэтому главное в этих тестах не
скорость, а то, что кеш ОТПУСКАЕТ: правка параметра, скрипта, объявления
проверки, чужого файла данных — пересчёт.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path

import pytest

from datum.checks.result import Artifact, CheckResult
from datum.doc import checkcache
from datum.doc.project import Project
from datum.tools import ops

LATCH = Path(__file__).resolve().parents[3] / "examples" / "latch"


@pytest.fixture()
def latch(tmp_path):
    root = tmp_path / "latch"
    shutil.copytree(LATCH, root, ignore=shutil.ignore_patterns(".datum", "output"))
    ops.clear_project_cache()
    yield root
    ops.clear_project_cache()


def _run(root, **kw):
    pr = Project.open(root)
    run = pr.run_checks(**kw)
    return pr, run


def _bump(path: Path, text: str) -> None:
    """Записать файл с заведомо новым временем правки."""
    path.write_text(text, encoding="utf-8")
    t = time.time() + 2
    os.utime(path, (t, t))


def _same(a, b) -> bool:
    """Тот же ответ до поля. Числа — с допуском 1e-9: усилие вставки считает
    многопоточный решатель МКЭ, и от запуска к запуску оно гуляет в 12-м знаке
    (28.549170458159 против 28.549170458211 — порядок сложения в BLAS). Тест
    падал от этого через раз; устаревшее значение отличалось бы на проценты."""
    def close(x, y) -> bool:
        if isinstance(x, float) and isinstance(y, float):
            return x == pytest.approx(y, rel=1e-9, abs=1e-12)
        if isinstance(x, dict) and isinstance(y, dict):
            return x.keys() == y.keys() and all(close(x[k], y[k]) for k in x)
        if isinstance(x, (list, tuple)) and isinstance(y, (list, tuple)):
            return len(x) == len(y) and all(close(i, j) for i, j in zip(x, y))
        return x == y

    strip = lambda r: {k: v for k, v in r.as_dict().items() if k != "duration_ms"}  # noqa: E731
    return close([strip(r) for r in a.results], [strip(r) for r in b.results])


# ------------------------------------------------------------------ берёт
def test_an_unchanged_project_is_answered_from_the_cache_with_the_same_answer(latch):
    pr1, first = _run(latch)
    assert pr1.check_cache_use == (0, len(pr1.specs()))
    pr2, second = _run(latch)
    assert pr2.check_cache_use == (len(pr2.specs()), len(pr2.specs()))
    assert _same(first, second), "ответ из кеша — тот же до поля"
    assert second.ok == first.ok and second.report() == first.report()


def test_fresh_recomputes_everything(latch):
    _run(latch)
    pr, run = _run(latch, fresh=True)
    assert pr.check_cache_use == (0, 0) and run.results


def test_the_manifest_can_switch_the_cache_off(latch):
    toml = latch / "datum.toml"
    toml.write_text(toml.read_text("utf-8") + "\n[checks]\ncache = false\n", encoding="utf-8")
    _run(latch)
    pr, _ = _run(latch)
    assert pr.check_cache_use == (0, 0)
    assert not (latch / ".datum" / "cache" / "checks.sqlite").exists()


# ------------------------------------------------------------------ отпускает
def test_a_parameter_change_recomputes_exactly_the_checks_that_read_it(latch):
    _, first = _run(latch)
    params = latch / "params" / "base.yaml"
    text = params.read_text("utf-8")
    assert 'LIP_H:   {value: 3.0' in text
    params.write_text(text.replace('LIP_H:   {value: 3.0', 'LIP_H:   {value: 2.6'),
                      encoding="utf-8")
    pr, second = _run(latch)
    reused, asked = pr.check_cache_use
    reads_it = {r.id for r in first.results if "LIP_H" in r.depends_on}
    not_reading = [s for s in pr.specs()
                   if not any(r.id == s.id or r.id.startswith(s.id + ".")
                              for r in first.results if "LIP_H" in r.depends_on)]
    assert reads_it and 0 < reused == len(not_reading) < asked
    # Пересчитанное — про НОВОЕ значение: то же, что даёт прогон без кеша.
    _, truth = _run(latch, fresh=True)
    assert _same(second, truth)
    assert not _same(first, second)


def test_a_failing_value_is_never_hidden_by_an_old_pass(latch):
    _, first = _run(latch)
    assert first.ok
    params = latch / "params" / "base.yaml"
    text = params.read_text("utf-8")
    # Губка в размер зазора — зацеп пропадает. Диапазон догадки 0.3…1.5.
    assert "value: 0.8" in text
    params.write_text(text.replace("value: 0.8", "value: 0.3", 1), encoding="utf-8")
    _, second = _run(latch)
    assert not second.ok, "кеш не должен вернуть прежнее «прошло»"
    assert {r.id for r in second.failed} >= {"крышка.зацеп.+Z"}


@pytest.mark.parametrize("victim", ["parts", "checks/checks.yaml", "assembly.yaml",
                                    "datum.toml", "свой файл"])
def test_touching_code_or_declarations_drops_the_whole_cache(latch, victim):
    _run(latch)
    if victim == "parts":
        path = next((latch / "parts").glob("*.py"))
        _bump(path, path.read_text("utf-8") + "\n# правка\n")
    elif victim == "свой файл":
        _bump(latch / "data.csv", "1;2;3\n")
    else:
        path = latch / victim
        _bump(path, path.read_text("utf-8") + "\n")
    pr, _ = _run(latch)
    assert pr.check_cache_use[0] == 0, f"после правки {victim} из кеша брать нельзя"


def test_notes_and_values_only_files_do_not_drop_the_cache(latch):
    _run(latch)
    _bump(latch / "NOTES.md", "заметки\n")
    _bump(latch / "JOURNAL.md", "- 2026-11-22 [решение] x\n")
    (latch / "output").mkdir(exist_ok=True)
    _bump(latch / "output" / "x.3mf", "выгрузка")
    pr, _ = _run(latch)
    assert pr.check_cache_use[0] == len(pr.specs())


def test_overrides_are_part_of_the_key(latch):
    _, base = _run(latch)
    _, tight = _run(latch, overrides={"LIP_D": 0.3})
    assert base.ok and not tight.ok
    _, again = _run(latch)
    assert again.ok, "подстановка не должна отравить ответ для принятых значений"


def test_an_error_is_not_remembered(latch):
    custom = latch / "checks" / "custom.py"
    flag = latch / "data.flag"
    custom.write_text(
        "from pathlib import Path\n"
        "from datum.doc.project import check\n"
        "from datum.checks.result import CheckResult\n\n\n"
        "@check('своя.капризная')\n"
        "def moody(P):\n"
        "    if not (Path(__file__).parent.parent / 'ok.marker.md').exists():\n"
        "        raise RuntimeError('не вышло')\n"
        "    return CheckResult(id='своя.капризная', title='x', status='pass', message='ок')\n",
        encoding="utf-8")
    assert not flag.exists()
    _, first = _run(latch)
    assert any(r.id == "своя.капризная" and r.status == "error" for r in first.results)
    # Причина ошибки ушла (файл .md в отпечаток не входит — кеш остальных цел).
    (latch / "ok.marker.md").write_text("", encoding="utf-8")
    pr, second = _run(latch)
    assert any(r.id == "своя.капризная" and r.status == "pass" for r in second.results)
    assert pr.check_cache_use[0] == len(pr.specs()) - 1


def test_a_cached_answer_whose_picture_is_gone_is_recomputed(tmp_path):
    cache = checkcache.CheckCache(tmp_path, context="к")
    picture = tmp_path / "p.png"
    picture.write_bytes(b"png")
    result = CheckResult(id="a", title="a", status="fail", message="m", threshold=(1.0, 2.0),
                         depends_on=("X",), inputs=("деталь",),
                         evidence=(Artifact(kind="png", caption="срез", path="p.png",
                                            url="/obj/p.png"),))
    assert cache.store("a", {"X": 1.0}, [result])
    back = cache.lookup("a", {"X": 1.0, "Y": 5.0})
    assert back == [result], "запись читается обратно той же, вместе с кортежем порога"
    assert cache.lookup("a", {"X": 1.5}) is None
    picture.unlink()
    assert cache.lookup("a", {"X": 1.0}) is None
    assert cache.store("a", {"X": 1.0}, [CheckResult(id="a", title="a", status="error",
                                                    message="m")]) is False
    cache.close()


def test_the_report_says_how_much_came_from_the_cache(latch):
    asyncio.run(ops.check_run(str(latch)))
    ops.clear_project_cache()
    out = asyncio.run(ops.check_run(str(latch)))
    assert out["cached"]["reused"] == out["cached"]["specs"] > 0
    assert "Из кеша:" in out["report"] and "fresh=true" in out["report"]
    ops.clear_project_cache()
    fresh = asyncio.run(ops.check_run(str(latch), fresh=True))
    assert fresh["cached"] == {"reused": 0, "specs": 0} and "Из кеша" not in fresh["report"]
