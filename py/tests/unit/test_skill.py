"""Навык Datum (`plugin/skills/datum/`): бюджет, ссылки, числа общие с кодом.

БЮДЖЕТ. После сжатия контекста Claude Code возвращает от каждого вызванного
навыка только первые 5000 токенов (всего 25 000) — документация
(code.claude.com/docs/en/skills.md, «Skill content lifecycle»). Наш SKILL.md
был 17 762 токена (замерено: разница входных токенов запроса с ним и без,
одинаково на haiku, sonnet и opus), и в долгой сессии агент терял две трети:
правила мастерской, «чего не делать», чужие модели. Теперь ядро — 3936
токенов (0.275 токена на байт), подробности — в `references/` (идея из Kadr).
Предел здесь — в байтах: 15 000 ≈ 4100 токенов, с запасом до 5000.

ОДИН УРОВЕНЬ. Каждый справочник назван в SKILL.md с моментом, когда его
читать; справочника, на который никто не ссылается, нет — его не прочтут.

ОДИН ФАКТ — ОДНО МЕСТО. Числа, которые держит код, сверяются с ним: иначе
навык уверенно называл бы прежний порог.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[3] / "plugin" / "skills" / "datum"
MAX_BODY_BYTES = 15_000
MAX_LINES = 500                                # совет документации
MAX_DESCRIPTION = 1536                         # длиннее в списке навыков обрезается


def _split() -> tuple[str, str]:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n"), "SKILL.md начинается с заголовка YAML"
    front, body = text[4:].split("\n---\n", 1)
    return front, body


def test_the_core_fits_what_survives_a_compaction():
    _, body = _split()
    size = len(body.encode("utf-8"))
    assert size <= MAX_BODY_BYTES, (
        f"тело SKILL.md {size} байт > {MAX_BODY_BYTES} (≈ {size * 0.275:.0f} токенов): "
        f"после сжатия хвост потеряется — вынесите подробности в references/")
    assert len(body.splitlines()) < MAX_LINES


def test_the_description_says_when_to_use_it():
    import yaml

    front, _ = _split()
    meta = yaml.safe_load(front)
    assert meta["name"] == "datum"
    description = " ".join(str(meta["description"]).split())
    assert len(description) <= MAX_DESCRIPTION
    assert "Использовать" in description and "datum.toml" in description


def test_every_reference_is_named_and_exists():
    _, body = _split()
    named = set(re.findall(r"references/([\w-]+\.md)", body))
    shipped = {p.name for p in (SKILL / "references").glob("*.md")}
    assert named <= shipped, f"нет файлов: {sorted(named - shipped)}"
    assert shipped <= named, f"справочники без ссылки из SKILL.md: {sorted(shipped - named)}"


def test_references_mention_only_references_that_exist():
    shipped = {p.name for p in (SKILL / "references").glob("*.md")}
    for path in (SKILL / "references").glob("*.md"):
        text = path.read_text(encoding="utf-8")
        for name in re.findall(r"`(?:references/)?([a-z]+\.md)`", text):
            assert name in shipped, f"{path.name} ссылается на несуществующий {name}"


def _all_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(SKILL.rglob("*.md")))


@pytest.mark.parametrize("phrase, value", [
    (r"Кругов не больше двух", lambda: __import__("datum.doc.critic", fromlist=["x"]).MAX_REVIEWS == 2),
    (r"не\s+теснее 0\.6 мм", lambda: __import__("datum.geom.text", fromlist=["x"]).KERN_MIN_GAP_MM == 0.6),
    (r"Процессорного времени на операцию — 15 минут",
     lambda: __import__("datum.build.worker", fromlist=["x"]).WorkerLimits().cpu_seconds == 15 * 60),
    (r"астрономического — 20",
     lambda: __import__("datum.build.worker", fromlist=["x"]).WorkerLimits().wall_seconds == 20 * 60),
    (r"при пороге 1\.0", lambda: _default("escape", "hold_vol") == 1.0),
    (r"нависания круче 45° требуют поддержек", lambda: _default("overhang", "limit_deg") == 45.0),
    (r"мосты до 30 мм", lambda: _default("bridge_span", "max_span") == 30.0),
    (r"стенки ≥ 1\.6 мм", lambda: _default("min_wall", "wall") == 1.6),
])
def test_numbers_the_skill_shares_with_the_code(phrase, value):
    text = " ".join(_all_text().split())
    assert re.search(phrase, text), f"в навыке нет «{phrase}» — правило переписано?"
    assert value(), f"навык говорит «{phrase}», а код — другое: поправьте одно из двух"


def _default(check: str, arg: str):
    import inspect

    from datum.checks.library import LIBRARY

    return inspect.signature(LIBRARY[check].fn).parameters[arg].default


def test_nothing_was_lost_in_the_split():
    """Разделы прежнего навыка живут дальше — в ядре или в справочнике."""
    text = " ".join(_all_text().split())
    for must in ("«Держит» — это не касание", "За подвижной деталью не может быть корпуса",
                 "Деталь — в ЦЕНТР стола", "Уголок куба", "Правила мастерской",
                 "Чего не делать", "Ложемент по скану", "Gridfinity",
                 "Провал приносит картинку", "Что можно в скрипте детали",
                 "Материал проекта", "Все виды отказа", "Подвижное звено объявляют шарниром",
                 "Органика и скульптура", "Продавленный объём — не сила"):
        assert must in text, must
