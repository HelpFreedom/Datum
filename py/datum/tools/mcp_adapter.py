"""Построение MCP-сервера из реестра операций.

Схема каждого инструмента выводится из сигнатуры функции — руками ничего не
дублируется. Поэтому набор инструментов у агента ровно тот же, что набор команд
у человека.
"""

from __future__ import annotations

import sys

from mcp.server.mcpserver import MCPServer

from datum.tools.registry import OPS

__all__ = ["build_server", "INSTRUCTIONS", "ARG_ERROR"]

INSTRUCTIONS = """\
Datum — инструмент проектирования деталей для 3D-печати.

Устройство проекта: params/base.yaml (параметры с происхождением), parts/*.py
(скрипты деталей), lib/ (общие помощники: from lib.gears import involute),
assembly.yaml (порядок сборки и способ постановки), checks/checks.yaml
(проверки из каталога), checks/custom.py (проектные проверки).

Правила, которые стоит держать в голове:
  * «держит» — это НЕ касание. Деталь обязана свободно выбрать печатный зазор и
    только потом упереться заметным объёмом (проверка escape). Пренебрежение
    этим стоило трёх напечатанных версий в проекте phone-case-gears.
  * У каждого числа есть происхождение. Угаданное объявляется как assumed с
    диапазоном; sweep_assumed показывает, какое из неизвестных решает дело, а
    какие мерить незачем.
  * Запаянная внутренняя полость — брак: её нечем заполнить. Страж ловит такие
    при сборке.
  * Снаружи не видно главного: перед выводами берите section (image=True —
    картинка приходит прямо в ответе) или occupancy_map.
  * Окна вы не видите. Человек говорит «эта деталь», «вот здесь», «мой
    замер» — window_state: что выделено, что скрыто, откуда смотрит камера,
    что выбрано штангенциркулем и какое число показано. Весь стол картинкой
    (детали на своих местах, образцы, вид «из окна», выбранное отметками) —
    scene_view. Показать человеку то, о чём говорите (деталь, срез, шарнир,
    подпись), — window_show: меняется вид, а не проект, и человек вернёт его
    одной кнопкой.
  * Параметр меняют операцией param_set, а не правкой YAML: производный не
    правится, угаданный держится диапазона, замер приходит с источником
    (source=...) и становится measured.
  * Замысел объявляют, а не угадывают. Как деталь ставится — в assembly.yaml:
    method: rigid (вдвигается, не задевая) или snap (защёлка, с прогибом).
    insert — направление ВСТАВКИ (куда деталь движется, становясь на место),
    а не снятия.
    Подвижное звено (крышка на петле, ползун) — там же, в joints: (kind,
    axis, origin, range), и проверка хода выводится сама.
  * Ориентация печати — у детали: @part(..., print_up="+X"). Проверки
    печатности берут её сами и НЕ выбирают; не записана — спросите человека.
  * Строите по фазам — и после каждой смотрите part_view (четыре вида в PNG,
    приходят прямо в ответе) и показываете человеку, прежде чем идти дальше.
  * Файл для слайсера — export (3MF/STL в позе печати, с рецептом). «Не готов
    к печати» в его отчёте передайте человеку, а не спрячьте.
  * Ложемент под реальную вещь — footprint по импортированному скану; ящики
    Gridfinity — datum.std.gridfinity, основание руками не рисуют.
  * Узел проверяют на ВСЕ виды отказа, а не на один: ось — срез, смятие и
    изгиб (pin_joint); винт, балка, подшипник, лишние связи — в каталоге
    (check_list). Пик МКЭ, названный сингулярностью, с допускаемым не сравнивают.
"""


def build_server(name: str = "datum", version: str = "0.1.0") -> MCPServer:
    """Собрать сервер из всех операций, помеченных для MCP."""
    import datum.tools.ops  # noqa: F401  — объявление операций

    server = MCPServer(name=name, title="Datum", version=version,
                       instructions=INSTRUCTIONS)
    from datum.tools.remote import endpoint_from_env, routed_callable

    # Работающий сервер — значит открыто окно. Тогда вызовы идут ТУДА, и всё,
    # что делает агент, видно в окне немедленно: то же задание, те же события.
    # Куда идти, решается на каждый вызов (см. `routed_callable`): сервер из
    # рукопожатия, сервер по объявлению для проекта вызова или свой процесс.
    endpoint = endpoint_from_env()

    for spec in OPS.values():
        if not spec.mcp:
            continue
        # Ответ — текстом (компактный JSON) и картинками, без структуры: см.
        # `_agent_tool`. Схема аргументов по-прежнему выводится из сигнатуры.
        server.add_tool(_agent_tool(routed_callable(spec, endpoint), endpoint),
                        name=spec.name, title=spec.title, description=spec.description,
                        structured_output=False)
        # `add_tool` сервера инструмент не возвращает — берём у распорядителя.
        _strict(server._tool_manager.get_tool(spec.name))
    return server


#: Сколько картинок прикладывается к одному ответу и какой самая большая.
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 1_500_000


def _image_paths(value, found: list[str]) -> list[str]:
    """Пути PNG из ответа операции — в порядке появления (`image`, `images`,
    доказательства проверок)."""
    if isinstance(value, dict):
        path = value.get("path")
        if isinstance(path, str) and path.lower().endswith(".png") and path not in found:
            found.append(path)
        for v in value.values():
            _image_paths(v, found)
    elif isinstance(value, list):
        for v in value:
            _image_paths(v, found)
    return found


def _agent_tool(call, endpoint):
    """Инструмент MCP: ответ операции — компактным JSON и САМИМИ картинками.

    Картинки. Прежде операция отдавала путь, и агент читал PNG отдельным
    вызовом `Read`: каждый вид стоил двух ходов, а ход при длинном контексте —
    это перечитывание всего контекста (разбор сессий: 56…104 чтения картинок
    на сессию). Claude Code показывает модели картинку из ответа инструмента
    сразу. Путь в ответе остаётся — показать человеку, приложить к отчёту.

    Текст. Словарь SDK сериализует с `indent=2` (в полтора раза больше знаков)
    и вдобавок шлёт тот же ответ вторым полем — структурой. Здесь он один и
    компактный.
    """
    import functools
    import json
    import os

    from mcp.server.mcpserver import Image

    @functools.wraps(call)
    async def tool(**kwargs):
        result = await call(**kwargs)
        text = json.dumps(result, ensure_ascii=False, default=str)
        if not isinstance(result, dict) or result.get("error"):
            return text
        root = str(kwargs.get("project") or (endpoint.project if endpoint else "") or "")
        images = []
        for rel in _image_paths(result, []):
            path = rel if os.path.isabs(rel) else os.path.join(root, rel)
            try:
                if os.path.getsize(path) > MAX_IMAGE_BYTES:
                    continue
            except OSError:
                continue
            images.append(Image(path=path))
            if len(images) >= MAX_IMAGES:
                break
        return [text, *images] if images else text

    signature = getattr(call, "__signature__", None)
    if signature is not None:
        tool.__signature__ = signature          # type: ignore[attr-defined]
    return tool


#: Ключ, под которым отказ в аргументах доезжает до обёртки операции.
ARG_ERROR = "__datum_arg_error__"


def _strict(tool) -> None:
    """Неизвестный аргумент — отказ словами, а не молчаливая потеря.

    Модель аргументов SDK лишнее отбрасывает: `part_build(part="крышка")`
    собирал ВСЕ детали (верное имя — `parts`), `part_delete(dryrun=true)`
    удалял по-настоящему. До проверки REST (`validate_args`, там лишнее
    запрещено) вызов доходил уже очищенным. Текст отказа — по-русски и едет
    до обёртки операции (`remote.routed_callable`) под ключом `ARG_ERROR`: там
    он становится `ToolError`, то есть ошибкой инструмента (is_error), как и
    прежний отказ схемы. Бросить его прямо отсюда нельзя — исключение из
    проверки аргументов SDK превращает в безликое «Error executing tool».
    """
    from pydantic import ValidationError

    from datum.tools.registry import unknown_arguments, validation_problems

    meta = tool.fn_metadata
    known = list(meta.arg_model.model_fields)

    class Strict(type(meta)):
        def validate_arguments(self, arguments_to_validate):
            given = arguments_to_validate if isinstance(arguments_to_validate, dict) else {}
            extra = unknown_arguments(known, given)
            if extra:
                return {ARG_ERROR: extra}
            try:
                return super().validate_arguments(arguments_to_validate)
            except ValidationError as e:
                return {ARG_ERROR: "; ".join(validation_problems(e))}

    tool.fn_metadata = Strict(arg_model=meta.arg_model, output_schema=meta.output_schema,
                              output_model=meta.output_model, wrap_output=meta.wrap_output)
    # И в схеме: клиент, который сверяет вызов сам, откажет ещё раньше.
    tool.parameters = {**tool.parameters, "additionalProperties": False}


def main(argv: list[str] | None = None) -> int:
    from datum.tools.remote import endpoint_from_env

    endpoint = endpoint_from_env()
    where = (f"через сервер окна на порту {endpoint.port}" if endpoint else
             "через сервер проекта, если он поднят, иначе в своём процессе")
    print(f"[datum-mcp] старт stdio-сервера, операции {where}",
          file=sys.stderr, flush=True)
    build_server().run("stdio")
    return 0
