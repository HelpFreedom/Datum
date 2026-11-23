"""Единый реестр операций.

Требование, ради которого он существует: человек и агент должны иметь ровно
одинаковые возможности. Расхождение между «что может кнопка» и «что может
агент» — источник сюрпризов, а сводить два списка руками никто долго не будет.

Поэтому операция объявляется ОДИН раз обычной асинхронной функцией с
аннотациями, а поверхности выводятся из неё:

  * MCP-инструмент — схема берётся из сигнатуры (`MCPServer.add_tool`);
  * команда CLI    — argparse строится из той же сигнатуры;
  * (позже) REST   — маршрут оттуда же.

Соглашение о результате: операция возвращает словарь. Ключ `report` — текст для
человека; остальное — машинные данные. Код возврата CLI считается функцией
`exit_code`, объявленной у операции, а не выдумывается адаптером.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, get_args, get_origin, get_type_hints

from datum.finite import non_finite

__all__ = ["Arg", "OpSpec", "ArgSpec", "op", "OPS", "clear_ops", "Danger",
           "validate_args", "ArgumentsError", "validation_problems",
           "unknown_arguments"]

Danger = Literal["safe", "mutates", "destructive"]


def Arg(help: str = "", metavar: str = "") -> Any:
    """Пояснение к аргументу: `Annotated[тип, Arg(help="…")]`.

    Возвращает pydantic-поле, а не собственный объект, и это существенно: иначе
    описание аргумента не попадает в схему MCP, и агент видит голое имя вроде
    `overrides`, не зная, что значения там в канонических единицах. Разбор
    командной строки читает то же самое поле.
    """
    from pydantic import Field

    return Field(description=help or None,
                 json_schema_extra={"datum_metavar": metavar} if metavar else None)


def _arg_meta(annotation: Any) -> tuple[str, str]:
    """Достать (пояснение, имя-в-справке) из Annotated."""
    if get_origin(annotation) is not Annotated:
        return "", ""
    for extra in get_args(annotation)[1:]:
        desc = getattr(extra, "description", None)
        if desc is not None or hasattr(extra, "json_schema_extra"):
            jse = getattr(extra, "json_schema_extra", None) or {}
            metavar = jse.get("datum_metavar", "") if isinstance(jse, dict) else ""
            return desc or "", metavar
    return "", ""


@dataclass(frozen=True, slots=True)
class ArgSpec:
    """Разобранный аргумент операции."""

    name: str
    annotation: Any
    default: Any = inspect.Parameter.empty
    help: str = ""
    metavar: str = ""

    @property
    def required(self) -> bool:
        return self.default is inspect.Parameter.empty

    @property
    def base_type(self) -> Any:
        """Тип без Optional и без Annotated."""
        a = self.annotation
        if get_origin(a) is Annotated:
            a = get_args(a)[0]
        args = get_args(a)
        if args and type(None) in args:
            rest = [x for x in args if x is not type(None)]
            if len(rest) == 1:
                return rest[0]
        return a

    @property
    def is_optional(self) -> bool:
        a = self.annotation
        if get_origin(a) is Annotated:
            a = get_args(a)[0]
        return type(None) in get_args(a)


@dataclass(frozen=True, slots=True)
class OpSpec:
    name: str
    fn: Callable[..., Any]
    title: str
    summary: str
    group: str = ""
    danger: Danger = "safe"
    cli: bool = True
    mcp: bool = True
    cli_name: str = ""
    exit_code: Callable[[dict], int] | None = None
    isolate: bool = False
    """Выполнять в отдельном процессе: с пределами по памяти и времени и с отменой.

    Ставится тем операциям, которые исполняют код из проекта. Скрипт детали —
    обычный Python: он может зациклиться или съесть всю память, и в общем
    процессе унёс бы с собой всё приложение.
    """
    memory_headroom_mb: int | None = None
    """Запас памяти изолированного процесса сверх родителя, если умолчания мало.

    Умолчание (`WorkerLimits.headroom_mb`) рассчитано на скрипт детали. Операции
    над чужим мешем память нужна по размеру файла — они объявляют её здесь, а
    применяется запас в одном месте, `build.worker.limits_for`.
    """
    args: tuple[ArgSpec, ...] = field(default_factory=tuple)

    @property
    def command(self) -> str:
        return self.cli_name or self.name

    @property
    def description(self) -> str:
        """Описание для агента: суть плюс пометка о последствиях."""
        # «safe» — проект не меняется. Кеш сборок в .datum/ пополняться может:
        # это не правка проекта, а запомненный расчёт, и обещать «ничего не
        # пишет» про part_build было бы неправдой.
        tail = {"safe": "Проект не изменяет (кеш расчётов в .datum/ не в счёт).",
                "mutates": "Изменяет проект на диске.",
                "destructive": "Необратимо изменяет или удаляет данные."}[self.danger]
        return f"{self.summary.strip()} {tail}"


#: Все объявленные операции.
OPS: dict[str, OpSpec] = {}


def clear_ops() -> None:
    OPS.clear()


def _parse_args(fn: Callable) -> tuple[ArgSpec, ...]:
    """Разобрать сигнатуру. Из неё выводятся и схема MCP, и разбор CLI."""
    sig = inspect.signature(fn)
    try:
        hints = get_type_hints(fn, include_extras=True)
    except Exception:                                   # pragma: no cover
        hints = {}
    out: list[ArgSpec] = []
    for name, p in sig.parameters.items():
        if name in ("self", "ctx"):                     # pragma: no cover
            continue
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            raise TypeError(f"операция {fn.__name__}: *args/**kwargs не поддерживаются — "
                            f"из них нельзя вывести ни схему, ни разбор командной строки")
        ann = hints.get(name, p.annotation)
        help_text, metavar = _arg_meta(ann)
        out.append(ArgSpec(name=name, annotation=ann, default=p.default,
                           help=help_text, metavar=metavar))
    return tuple(out)


def _plain(value: Any) -> Any:
    """Ответ операции — обычными типами Python: числа numpy (`numpy.int32`
    числа степеней свободы МКЭ) не превращаются в JSON у FastAPI и MCP, и
    ответ `fem_verify`/`fem_solve` терялся ПОСЛЕ расчёта — HTTP 500 и
    «UnexpectedToolError» (найдено обзором; CLI выручал `default=str`)."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    module = type(value).__module__
    if module == "numpy":
        return value.tolist() if hasattr(value, "tolist") else value.item()
    return value


def op(name: str, *, title: str, summary: str, group: str = "",
       danger: Danger = "safe", cli: bool = True, mcp: bool = True,
       cli_name: str = "", exit_code: Callable[[dict], int] | None = None,
       isolate: bool = False, memory_headroom_mb: int | None = None):
    """Объявить операцию Datum.

    summary — одно-два предложения о сути: их читает и человек в справке, и агент
    в описании инструмента. Писать надо так, чтобы по ним было понятно, когда
    операцию звать.
    """

    def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"операция {name!r} должна быть асинхронной: "
                            f"MCP вызывает её как корутину")
        if name in OPS:
            raise ValueError(f"операция {name!r} уже объявлена")

        # Страж конечных чисел — ЗДЕСЬ, а не в адаптерах: сюда вызов приходит
        # уже с приведёнными типами с любой поверхности (CLI — argparse, MCP и
        # REST — pydantic, который строку "nan" честно делает числом). Обёртка
        # и есть то, что возвращается модулю: воркер передаёт функцию pickle
        # по имени, и имя в модуле обязано указывать на тот же объект.
        positional = [p.name for p in inspect.signature(fn).parameters.values()]

        @functools.wraps(fn)
        async def guarded(*args: Any, **kwargs: Any) -> Any:
            for key, value in [*zip(positional, args), *kwargs.items()]:
                bad = non_finite(value, key)
                if bad is not None:
                    where, number = bad
                    message = (f"{name}: {where} = {number} — не число. Нужны конечные "
                               f"числа: nan и бесконечность не бывают ни размером, ни "
                               f"положением, ни пределом")
                    return {"error": message, "report": f"ОШИБКА: {message}"}
            result = _plain(await fn(*args, **kwargs))
            # Ответ с nan — не ответ: «габарит nan×nan мм», «прогиб nan» уходили
            # агенту как замер. Ищется по числам ответа, а не по тексту.
            bad = non_finite(result, "ответ", allow_inf=True) if isinstance(result, dict) else None
            if bad is not None and not result.get("error"):
                message = (f"{name}: в ответе нечисло ({bad[0].removeprefix('ответ.')} = "
                           f"{bad[1]}) — расчёт выродился, верить ему нельзя. Проверьте "
                           f"входы: числа вне разумного или вырожденная геометрия")
                return {"error": message, "report": f"ОШИБКА: {message}"}
            return result

        OPS[name] = OpSpec(name=name, fn=guarded, title=title, summary=summary,
                           group=group, danger=danger, cli=cli, mcp=mcp,
                           cli_name=cli_name, exit_code=exit_code,
                           isolate=isolate, memory_headroom_mb=memory_headroom_mb,
                           args=_parse_args(fn))
        return guarded

    return wrap



# ------------------------------------------------------------ проверка аргументов
class ArgumentsError(ValueError):
    """Аргументы операции не подходят под её сигнатуру."""


_MODELS: dict[str, Any] = {}


def _model(spec: OpSpec):
    """Модель аргументов операции — из её же сигнатуры, как у MCP."""
    if spec.name in _MODELS:
        return _MODELS[spec.name]
    from pydantic import ConfigDict, create_model

    hints = get_type_hints(spec.fn, include_extras=True)
    fields: dict[str, Any] = {}
    for name, param in inspect.signature(spec.fn).parameters.items():
        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[name] = (hints.get(name, Any), default)
    model = create_model(f"{spec.name}_args", __config__=ConfigDict(extra="forbid"),
                         **fields)
    _MODELS[spec.name] = model
    return model


def validate_args(spec: OpSpec, args: dict) -> dict:
    """Проверить аргументы по сигнатуре операции и привести типы.

    Три поверхности обязаны вести себя одинаково. CLI проверяет разбором
    командной строки, MCP — схемой, а REST долго не проверял ничем: строка на
    месте списка деталей проходила как список букв, число на месте тега роняло
    операцию `AttributeError`, лишний ключ — `TypeError` изнутри. Здесь та же
    схема, что у MCP: модель выводится из сигнатуры.
    """
    from pydantic import ValidationError

    try:
        valid = _model(spec).model_validate(args)
    except ValidationError as e:
        raise ArgumentsError(f"операция {spec.name}: "
                             + "; ".join(validation_problems(e))) from None
    return {name: getattr(valid, name) for name in type(valid).model_fields}


#: Отказы pydantic — по-русски. Прежде агент получал «Field required … For
#: further information visit https://errors.pydantic.dev/…» и тратил ход на
#: догадку; тип ошибки у pydantic устойчив, текст — нет, поэтому по типу.
_PROBLEM_TEXT = {
    "missing": "обязателен, но не передан",
    "extra_forbidden": "такого аргумента у операции нет",
    "literal_error": "недопустимое значение",
    "string_type": "нужна строка",
    "int_type": "нужно целое число", "int_parsing": "нужно целое число",
    "int_from_float": "нужно целое число",
    "float_type": "нужно число", "float_parsing": "нужно число",
    "bool_type": "нужно да/нет (true/false)", "bool_parsing": "нужно да/нет (true/false)",
    "list_type": "нужен список", "dict_type": "нужен словарь «ключ: значение»",
}


def validation_problems(e: Any) -> list[str]:
    """Что не так в аргументах — строками «где: что», по-русски."""
    problems = []
    for err in e.errors():
        where = ".".join(str(x) for x in err.get("loc", ())) or "аргументы"
        kind = err.get("type", "")
        text = _PROBLEM_TEXT.get(kind) or err.get("msg", "")
        if kind == "literal_error":
            expected = (err.get("ctx") or {}).get("expected")
            if expected:
                text += f" {err.get('input')!r}; допустимо: {expected}"
        problems.append(f"{where}: {text}")
    return problems


def unknown_arguments(known: list[str], given: Any) -> str:
    """Отказ за лишние аргументы — с именами настоящих и ближайшим похожим.

    Нужен там, где схема лишнее молча отбрасывает (MCP): `part_build(part=…)`
    собирал ВСЕ детали, `part_delete(dryrun=true)` удалял по-настоящему.
    Пустая строка — лишних нет.
    """
    import difflib

    extra = [k for k in given if k not in known]
    if not extra:
        return ""
    notes = []
    for key in extra:
        low = {k.lower().replace("_", ""): k for k in known}
        near = low.get(str(key).lower().replace("_", "")) or next(
            iter(difflib.get_close_matches(str(key), known, n=1, cutoff=0.6)), "")
        notes.append(f"«{key}»" + (f" (возможно, {near})" if near else ""))
    return (("нет аргумента " if len(extra) == 1 else "нет аргументов ") + ", ".join(notes)
            + "; есть: " + (", ".join(known) if known else "ни одного"))
