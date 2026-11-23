"""Единый реестр операций.

Главное, что здесь проверяется: человек и агент имеют ОДИНАКОВЫЕ возможности.
Списки не ведутся руками, поэтому разойтись они не могут — но пусть это
подтверждает тест, а не намерение.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

import datum.tools.ops  # noqa: F401  — объявление операций
from datum.tools.cli_adapter import build_parser
from datum.tools.mcp_adapter import build_server
from datum.tools.registry import OPS


# ------------------------------------------------------------------ главное
def test_cli_and_mcp_offer_the_same_operations():
    parser = build_parser()
    sub = next(a for a in parser._actions if hasattr(a, "choices") and a.choices)
    # Подкоманды-серверы (`mcp`, `serve`) — не операции: они не делают ничего
    # с документом, они открывают дверь. Адаптер помечает их `_spec=None`,
    # и отбор идёт по этому признаку, а не по списку имён — иначе тест
    # расходился бы с кодом при каждой новой такой команде.
    cli_names = {name for name, p in sub.choices.items()
                 if p.get_default("_spec") is not None}

    tools = asyncio.run(build_server().list_tools())
    mcp_names = {t.name for t in tools}

    assert cli_names == {s.command for s in OPS.values() if s.cli}
    assert mcp_names == {s.name for s in OPS.values() if s.mcp}
    # Только для окна — поимённо, как и опасные: такая операция должна быть
    # осознанным решением. window_report — окно сообщает, что видит человек;
    # дай его агенту, и «что видит человек» можно было бы подделать.
    window_only = {s.name for s in OPS.values() if not s.cli and not s.mcp}
    assert window_only == {"window_report"}
    assert len(cli_names) == len(mcp_names) == len(OPS) - len(window_only), (
        "каждая операция, кроме оконных, обязана быть доступна обеим сторонам")


def test_every_operation_is_documented():
    for spec in OPS.values():
        assert spec.title, spec.name
        assert len(spec.summary) > 40, f"{spec.name}: слишком короткое описание"
        assert spec.summary.strip().endswith("."), f"{spec.name}: описание без точки"


def test_danger_is_stated_and_reaches_the_description():
    """Список умышленно перечислен поимённо.

    Появление новой операции, меняющей данные, должно быть осознанным решением,
    а не побочным следствием правки. Тест для того и падает.
    """
    assert {s.name for s in OPS.values() if s.danger == "mutates"} == {
        # snapshot_create  — пишет снимок версии в проект
        # mesh_repair      — пишет починенный меш в файл
        # import_model     — заводит imported/<имя>/ в проекте
        # param_set        — правит params/base.yaml по месту
        # export           — перезаписывает выгрузку в output/
        # imported_to_part — заводит parts/<имя>.py из импортированной модели
        # import_to_bed    — заводит модели в imported/ и детали в parts/ разом
        # critic_verdict   — пишет вердикт критика в .datum/critic.json
        # font_add         — копирует системный шрифт в fonts/ проекта
        # project_save     — пишет архив .datum (чужой файл не затирает)
        # project_unpack   — заводит каталог проекта из архива
        # part_place       — пишет положение детали в placement.yaml
        # part_delete      — уносит деталь целиком в корзину .datum/trash/
        # part_restore     — возвращает её оттуда
        # slice            — пишет проект слайсера и G-code в output/
        # param_add        — дописывает параметр в params/base.yaml
        # param_edit       — правит поля параметра (причина, диапазон, формула)
        # param_set_many   — несколько значений разом, все или ни одного
        # note_add         — строка в журнал решений JOURNAL.md
        # coupon           — пишет образцы посадки в output/coupons/
        # assembly_guide   — пишет инструкцию по сборке в output/guide/
        # sim_export       — пишет GLB, MJCF, URDF и сетки в output/sim/
        # component_save   — пишет компонент в общую библиотеку (вне проекта)
        # component_use    — дописывает параметры и файлы компонента в проект
        # print_log        — строка в журнал проекта и урок в общую базу опыта
        # project_rename   — название в datum.toml и, по просьбе, папка проекта
        # project_copy     — заводит новый проект копией рядом
        "snapshot_create", "mesh_repair", "import_model", "param_set", "export",
        "imported_to_part", "import_to_bed", "critic_verdict", "font_add", "project_save",
        "project_unpack",
        "part_place", "part_delete", "part_restore", "slice",
        "param_add", "param_edit", "param_set_many", "note_add", "coupon",
        "assembly_guide", "sim_export", "component_save", "component_use",
        "print_log", "project_rename", "project_copy"}
    assert {s.name for s in OPS.values() if s.danger == "destructive"} == {"cache_gc"}
    assert "Изменяет проект" in OPS["snapshot_create"].description
    assert "Необратимо" in OPS["cache_gc"].description
    assert "Проект не изменяет" in OPS["check_run"].description


def test_argument_help_reaches_both_surfaces():
    """Пояснение к аргументу пишется один раз и попадает и в схему, и в справку."""
    tools = asyncio.run(build_server().list_tools())
    t = next(x for x in tools if x.name == "snapshot_create")
    schema = t.inputSchema if hasattr(t, "inputSchema") else t.input_schema
    assert "канонических единицах" in schema["properties"]["overrides"]["description"]

    parser = build_parser()
    sub = next(a for a in parser._actions if hasattr(a, "choices") and a.choices)
    text = sub.choices["snapshot"].format_help()
    assert "канонических единицах" in text


# ------------------------------------------------------------------ разбор сигнатур
def test_required_arguments_become_positional():
    parser = build_parser()
    sub = next(a for a in parser._actions if hasattr(a, "choices") and a.choices)
    ns = parser.parse_args(["measure", "/путь", "а", "б"])
    assert ns.project == "/путь" and ns.a == "а" and ns.b == "б"
    assert ns.search == 4.0, "значение по умолчанию берётся из сигнатуры"


def test_optional_arguments_become_flags():
    parser = build_parser()
    ns = parser.parse_args(["params", "/путь", "--only-unknown"])
    assert ns.only_unknown is True
    assert parser.parse_args(["params", "/путь"]).only_unknown is False


def test_repeatable_arguments():
    parser = build_parser()
    ns = parser.parse_args(["build", "/путь", "--parts", "а", "--parts", "б"])
    assert ns.parts == ["а", "б"]


def test_key_value_arguments_are_parsed():
    from datum.tools.cli_adapter import _kv_pairs

    assert _kv_pairs(["A=1.5", "B=2"]) == {"A": 1.5, "B": 2.0}
    assert _kv_pairs(None) is None
    with pytest.raises(Exception, match="ИМЯ=ЗНАЧЕНИЕ"):
        _kv_pairs(["безравно"])
    with pytest.raises(Exception, match="не число"):
        _kv_pairs(["A=икс"])


def test_string_maps_keep_their_values_as_text():
    """`slice --overrides` объявлен `dict[str, str]`: правка профиля «тип
    поддержек = tree» из командной строки прежде отказывала «не число»."""
    from datum.tools.cli_adapter import _collect

    parser = build_parser()
    ns = parser.parse_args(["slice", "/путь", "--overrides", "support_type=tree(auto)",
                            "--overrides", "wall_loops=4"])
    kwargs = _collect(OPS["slice"], ns)
    assert kwargs["overrides"] == {"support_type": "tree(auto)", "wall_loops": "4"}
    # Числовые словари (подстановки параметров) по-прежнему — числа.
    ns = parser.parse_args(["snapshot", "/путь", "т", "--overrides", "GAP=0.3"])
    assert _collect(OPS["snapshot_create"], ns)["overrides"] == {"GAP": 0.3}


# ------------------------------------------------------------------ соглашения
def test_all_operations_are_async():
    for spec in OPS.values():
        assert inspect.iscoroutinefunction(spec.fn), spec.name


def test_every_result_carries_a_report():
    """Текст для человека — часть результата, а не выдумка адаптера."""
    res = asyncio.run(OPS["datum_status"].fn())
    assert "report" in res and isinstance(res["report"], str)
    res2 = asyncio.run(OPS["check_list"].fn())
    assert "report" in res2


def test_errors_come_back_as_data_not_exceptions():
    res = asyncio.run(OPS["project_info"].fn(project="/нет/такого"))
    assert "error" in res and "нет каталога" in res["error"]
    assert res["report"].startswith("ОШИБКА")


def test_registry_rejects_a_duplicate_name():
    from datum.tools.registry import op

    with pytest.raises(ValueError, match="уже объявлена"):
        @op("check_run", title="дубль", summary="Повтор имени существующей операции.")
        async def _dup() -> dict:
            return {}


def test_registry_rejects_a_sync_function():
    from datum.tools.registry import op

    with pytest.raises(TypeError, match="асинхронной"):
        @op("синхронная", title="т", summary="Операция, написанная не как корутина.")
        def _sync() -> dict:
            return {}


def test_registry_rejects_varargs():
    from datum.tools.registry import op

    with pytest.raises(TypeError, match=r"\*args"):
        @op("звёздочки", title="т", summary="Операция с переменным числом аргументов.")
        async def _va(*args) -> dict:
            return {}
