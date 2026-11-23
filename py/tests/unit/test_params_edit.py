"""Правка параметров по месту — из окна и от агента.

Файл параметров правится как текст: комментарии в нём записывают, почему число
такое, и правка не должна их стирать. Правила реестра при этом действуют и на
правку: производный не правится, угаданный держится своего диапазона, замер
приходит с источником.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
import yaml

from datum.doc.params import ParamRegistry
from datum.doc.params_edit import set_fields
from datum.tools.ops import param_set

LATCH = Path(__file__).resolve().parents[3] / "examples" / "latch"


@pytest.fixture
def work(tmp_path):
    root = tmp_path / "latch"
    shutil.copytree(LATCH, root, ignore=shutil.ignore_patterns(".datum"))
    return root


def _set(root, *a, **k):
    return asyncio.run(param_set(project=str(root), *a, **k))


def test_only_the_edited_value_changes(work):
    before = (work / "params" / "base.yaml").read_text(encoding="utf-8")
    out = _set(work, name="GAP", value=0.25)
    assert "error" not in out, out
    after = (work / "params" / "base.yaml").read_text(encoding="utf-8")
    changed = [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
    assert len(before.splitlines()) == len(after.splitlines())
    assert changed == [("GAP:     {value: 0.30, unit: mm, provenance: chosen,",
                        "GAP:     {value: 0.25, unit: mm, provenance: chosen,")]


def test_a_measurement_closes_the_unknown(work):
    out = _set(work, name="LIP_D", value=0.9, source="штангенциркуль, три места")
    assert out["provenance"] == "measured"
    p = ParamRegistry.load(work / "params" / "base.yaml")["LIP_D"]
    assert (p.provenance, p.display, p.range, p.blocking) == ("measured", 0.9, None, False)
    assert p.source == "штангенциркуль, три места"
    text = (work / "params" / "base.yaml").read_text(encoding="utf-8")
    assert "# Единственное угаданное число" in text, "комментарии обязаны остаться"


@pytest.mark.parametrize("name,value,source,word", [
    ("LIP_D", 2.0, "", "вне диапазона"),
    ("LID_Z", 5.0, "", "производный"),
    ("НЕТУ", 1.0, "", "нет"),
])
def test_edits_that_break_the_rules_are_refused(work, name, value, source, word):
    before = (work / "params" / "base.yaml").read_text(encoding="utf-8")
    out = _set(work, name=name, value=value, source=source)
    assert "error" in out and word in out["error"], out
    assert (work / "params" / "base.yaml").read_text(encoding="utf-8") == before


def test_a_measured_value_changes_only_with_a_new_source(work):
    _set(work, name="LIP_D", value=0.9, source="штангенциркуль")
    out = _set(work, name="LIP_D", value=0.95)
    assert "error" in out and "source" in out["error"]


def test_nested_conflicts_are_not_touched():
    """Поля вложенного списка расхождений называются так же; правка обязана
    попасть в сам параметр."""
    text = ("PHONE_T:\n  value: 8.2\n  unit: mm\n  provenance: measured\n"
            "  conflicts:\n    - value: 9.0\n      source: \"штангенциркуль\"\n"
            "  reason: \"x\"\n")
    out = set_fields(text, "PHONE_T", {"value": 8.9, "source": "сечение"})
    p = ParamRegistry.from_dict(yaml.safe_load(out))["PHONE_T"]
    assert p.display == 8.9 and p.source == "сечение"
    assert p.conflicts[0].value == 9.0 and p.conflicts[0].source == "штангенциркуль"
