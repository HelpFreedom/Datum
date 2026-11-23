"""Справочник API навыка собран из кода и с кодом совпадает.

Агент пишет скрипт детали по справочнику, а не по исходникам. Справочник,
отставший от кода, хуже отсутствующего: он уверенно называет прежнюю подпись.
Поэтому файл генерируется (`datum.tools.apiref`), и здесь он сверяется с тем,
что генерация выдаёт сейчас.
"""

from __future__ import annotations

import datum.build as build
import datum.geom as geom
from datum.tools.apiref import REFERENCE, render

REGENERATE = ".venv/bin/python -m datum.tools.apiref --write (из каталога py/)"


def test_the_reference_matches_the_code():
    assert REFERENCE.is_file(), f"нет {REFERENCE}: {REGENERATE}"
    assert REFERENCE.read_text(encoding="utf-8") == render(), \
        f"справочник API отстал от кода — пересоберите: {REGENERATE}"


def test_every_public_name_is_in_it():
    text = REFERENCE.read_text(encoding="utf-8")
    missing = [n for n in [*geom.__all__, *build.__all__]
               if f"`{n}" not in text and f"`{n}(" not in text]
    assert not missing, missing


def test_the_skill_points_at_it():
    skill = (REFERENCE.parent.parent / "SKILL.md").read_text(encoding="utf-8")
    assert "references/api.md" in skill
