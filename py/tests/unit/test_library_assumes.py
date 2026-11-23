"""Каждая проверка обязана объявить, что принимает на веру.

ЗАЧЕМ ЭТО ЗАПЕРТО ТЕСТОМ. Механизмов не перечислить — ни деталей, ни корпусов,
ни ситуаций, в которых та или иная проверка уместна. Соблазн закрыть эту дыру
РАСПОЗНАВАНИЕМ («сама пойму, защёлка это или затор») проверен дважды и дважды
провалился на замере: доля пути с касанием дала 0.52 / 0.68 / 0.84 — не
разделяется; обнуление задевания к концу пути одинаково выглядит у защёлки и у
запертой полости, потому что «продавил губку» и «прошёл сквозь стенку» —
геометрически одно событие.

Раз распознать нельзя, применимость ОБЪЯВЛЯЕТСЯ. Тест следит, чтобы объявление
не забыли и чтобы оно не выродилось в отписку: новая проверка не попадёт в
каталог, пока её автор не скажет, где она врёт, — а помнит он это ровно в тот
момент, когда её пишет, и никогда потом.
"""

from __future__ import annotations

import pytest

from datum.checks.library import LIBRARY

# ЧЕГО ЭТОТ ТЕСТ НЕ ПРОВЕРЯЕТ, И ЭТО ВАЖНО.
#
# Он не проверяет, что допущение ВЕРНО и что оно ПОЛЕЗНО. Первая версия
# пыталась: требовала в тексте слов вроде «не», «только», «вне» — мол, раз
# граница очерчена, слово найдётся. Тест немедленно покраснел на пяти честных
# допущениях, в том числе на «Законно тонкая деталь — плёнка, лепесток —
# попадёт под тот же порог по объёму», где граница названа совершенно ясно.
#
# Это была третья за день попытка подменить суждение детектором — после двух
# провалившихся различителей в `checks/assembly.py`. Такой тест не улучшает
# допущения, а учит вставлять волшебное слово. Поэтому машина проверяет ровно
# то, что умеет: поле есть, оно не пустое, не обрывок и не копия описания.
# Верно ли оно — решает человек, читающий код рядом с проверкой.


@pytest.mark.parametrize("name", sorted(LIBRARY))
def test_every_check_declares_what_it_takes_for_granted(name):
    entry = LIBRARY[name]
    assert entry.assumes, (
        f"у проверки {name!r} не объявлено допущение. Напишите его в `_entry(..., "
        f"assumes=...)`: что проверка принимает на веру и, значит, где она даёт "
        f"уверенный неверный ответ")


@pytest.mark.parametrize("name", sorted(LIBRARY))
def test_the_declaration_is_not_a_shrug(name):
    """Отписка вроде «обычные допущения» хуже пустоты: её видно в каталоге и ей
    верят."""
    assumes = LIBRARY[name].assumes
    assert len(assumes) >= 60, f"{name}: допущение слишком короткое, чтобы что-то сказать"
    assert assumes[-1] in ".!", f"{name}: допущение обрывается на {assumes[-1]!r}"
    assert len(assumes.split()) >= 10, f"{name}: допущение в несколько слов ничего не очертит"


def test_the_declaration_does_not_merely_repeat_the_title():
    """Допущение — не пересказ описания. Проверяется грубо, по совпадению слов:
    точнее автомат не умеет, а грубого хватает, чтобы поймать копипасту."""
    for name, e in sorted(LIBRARY.items()):
        doc_words = {w.lower().strip(".,:;—") for w in e.doc.split() if len(w) > 4}
        as_words = {w.lower().strip(".,:;—") for w in e.assumes.split() if len(w) > 4}
        if not doc_words:
            continue
        overlap = len(doc_words & as_words) / len(doc_words)
        assert overlap < 0.7, (
            f"{name}: допущение почти повторяет описание ({overlap:.0%} общих слов)")


def test_the_catalogue_shows_the_assumptions():
    """Объявление бесполезно, если его не видно там, где выбирают проверку."""
    import asyncio

    from datum.tools.ops import check_list

    out = asyncio.run(check_list())
    assert "принимает на веру" in out["report"]
    for name, e in LIBRARY.items():
        assert out["checks"][name]["assumes"] == e.assumes
        assert e.assumes in out["report"], f"{name}: допущения нет в отчёте каталога"


def test_the_skill_reference_knows_every_check():
    """Навык, разошедшийся с кодом, опаснее отсутствующего.

    Агент выбирает проверку по справочнику, а не по исходникам. Проверка,
    попавшая в каталог и не попавшая в `references/checks.md`, для него просто
    не существует — а хуже того, он может выбрать похожую с неподходящим
    допущением. Дважды такое уже случалось: `flexure_force_fem` и
    `insertion_force` прожили в каталоге по дню, не будучи там описаны.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[3]
    reference = root / ".claude" / "skills" / "datum" / "references" / "checks.md"
    assert reference.is_file(), f"справочника нет: {reference}"
    text = reference.read_text(encoding="utf-8")
    missing = sorted(name for name in LIBRARY if name not in text)
    assert not missing, (
        f"этих проверок нет в справочнике навыка: {missing}. "
        f"Допишите их в {reference.name} — иначе для агента они не существуют")
