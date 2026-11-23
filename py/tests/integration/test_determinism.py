"""Одна и та же программа — одна и та же деталь, в любом процессе.

Сверка кеша (`cache.verify`) пересобирает деталь в ТОМ ЖЕ процессе и не видит
того, что меняется от запуска к запуску: порядка обхода словарей и множеств
(он зависит от `PYTHONHASHSEED`), порядка сборки деталей, состояния, которое
одна деталь оставляет другой. Здесь все детали всех примеров собираются без
кеша в двух СВЕЖИХ процессах — с разным зерном хеширования, второй в обратном
порядке, — и формы (`Solid.digest`, канонический вид) обязаны совпасть.

Идея — из sand3d (тот так проверяет каждый свой глагол); примеры находятся
сами: новый пример попадёт в проверку без правки теста.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

BUILD_ALL = r"""
import json, sys
from pathlib import Path
from datum.doc.project import Project

reverse = sys.argv[1] == "rev"
projects = sorted(p.parent for p in Path("examples").glob("*/datum.toml"))
out = {}
for root in (projects[::-1] if reverse else projects):
    pr = Project.open(root)
    names = sorted(pr.parts)
    for name in (names[::-1] if reverse else names):
        res = pr.build([name], use_cache=False)[name]
        out[f"{root.name}/{name}"] = res.solid.digest() if res.ok else f"ОШИБКА: {res.error}"
print("@@digests " + json.dumps(out, ensure_ascii=False))
"""


def _digests(order: str, seed: str) -> dict[str, str]:
    env = {**os.environ, "PYTHONHASHSEED": seed}
    done = subprocess.run([sys.executable, "-c", BUILD_ALL, order], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=900)
    lines = [l for l in done.stdout.splitlines() if l.startswith("@@digests ")]
    assert lines, f"сборка не дала отпечатков:\n{done.stderr[-3000:]}"
    return json.loads(lines[-1].removeprefix("@@digests "))


def test_every_example_part_is_the_same_shape_in_two_fresh_processes():
    first = _digests("fwd", "1")
    second = _digests("rev", "4242")
    assert len(first) >= 10, f"собрано подозрительно мало деталей: {sorted(first)}"
    broken = {k: v for k, v in first.items() if v.startswith("ОШИБКА")}
    assert not broken, broken
    differ = sorted(k for k in first if first[k] != second.get(k))
    assert not differ, f"детали собираются по-разному от процесса к процессу: {differ}"
