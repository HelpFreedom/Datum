"""Контекст сборки: активные параметры и запись обращений к ним.

Скрипт детали пишется так:

    from datum.geom import *
    from datum.build import P, part

    @part("shutter")
    def build():
        return box(0, P.W, 0, P.H, 0, P.T)

`P` — не словарь, а прокси текущего прогона. Он живёт в ContextVar, поэтому
сборки не мешают друг другу, а каждое обращение записывается: из этих записей
получается `depends_on` детали, и никаких объявлений руками вести не нужно.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from datum.doc.params import ParamProxy

__all__ = ["P", "active_params", "use_params", "NoParamsError",
           "use_project", "project_root", "bed", "draft", "forget_project_lib", "PROJECT_LIB",
           "FreshSourceLoader"]

_ACTIVE: ContextVar[ParamProxy | None] = ContextVar("datum_active_params", default=None)

#: Корень проекта текущей сборки. Нужен скриптам, читающим чужие модели из
#: `imported/`: путь к ним не должен ни задаваться руками, ни выводиться из
#: `__file__` — первое даёт произвольный доступ к диску, второе ломается, как
#: только скрипт переедет в подкаталог.
_ROOT: ContextVar[object | None] = ContextVar("datum_project_root", default=None)

#: Стол принтера проекта, мм. Кладёт его проект — уже проверенным при открытии
#: (`doc.project._validate_manifest`), чтобы скрипт не читал datum.toml сам.
_BED: ContextVar[tuple[float, float, float] | None] = ContextVar("datum_bed", default=None)

#: Черновая сборка: скрипт сам решает, что огрубить (обычно шаг поля).
_DRAFT: ContextVar[bool] = ContextVar("datum_draft", default=False)


class NoParamsError(RuntimeError):
    """Обращение к параметрам вне сборки."""


def active_params() -> ParamProxy:
    proxy = _ACTIVE.get()
    if proxy is None:
        raise NoParamsError(
            "параметры доступны только во время сборки детали. Если вы запускаете "
            "скрипт напрямую, оберните вызов в use_params(registry.proxy())")
    return proxy


def project_root():
    """Корень проекта текущей сборки."""
    root = _ROOT.get()
    if root is None:
        raise NoParamsError(
            "корень проекта известен только во время сборки. Если вы запускаете "
            "скрипт напрямую, оберните вызов в use_project(путь)")
    return root


def bed() -> tuple[float, float, float]:
    """Стол принтера проекта (X, Y, Z), мм — `printer.bed` из datum.toml.

    Чтобы поставить деталь в центр стола — `return centre_on_bed(body)`
    (`datum.build.place`).

    Входит в ключ кеша сборки: сменили стол — деталь пересоберётся.
    """
    value = _BED.get()
    if value is None:
        raise NoParamsError(
            "стол принтера известен только во время сборки детали проекта. Если "
            "вы запускаете скрипт напрямую, оберните вызов в use_project(путь, bed=...)")
    return value


def draft() -> bool:
    """Черновая ли это сборка: форма и пропорции за секунды, точность — потом.

        h = 1.0 if draft() else 0.4          # шаг поля, мм

    Черновик — `part_build(draft=True)` и кнопка «Черновик» в окне. Входит в
    ключ кеша. Проверки и выгрузка черновика не видят НИКОГДА: они собирают
    деталь сами, обычной сборкой, — числа в проверках не бывают черновыми.
    Вне сборки — False.
    """
    return _DRAFT.get()


@contextmanager
def use_project(root, bed: tuple[float, float, float] | None = None, *,
                draft: bool = False) -> Iterator[object]:
    from pathlib import Path

    token = _ROOT.set(Path(root).resolve())
    bed_token = _BED.set(tuple(float(v) for v in bed) if bed is not None else None)
    draft_token = _DRAFT.set(bool(draft))
    try:
        yield _ROOT.get()
    finally:
        _DRAFT.reset(draft_token)
        _BED.reset(bed_token)
        _ROOT.reset(token)


@contextmanager
def use_params(proxy: ParamProxy) -> Iterator[ParamProxy]:
    token = _ACTIVE.set(proxy)
    try:
        yield proxy
    finally:
        _ACTIVE.reset(token)


class _ParamsFacade:
    """Тонкая обёртка: `P.WALL` работает внутри сборки и внятно ругается вне её."""

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return getattr(active_params(), name)

    def __getitem__(self, name: str) -> Any:
        return getattr(active_params(), name)

    def __setattr__(self, name: str, value: Any) -> None:    # pragma: no cover
        raise AttributeError("параметры менять из скрипта детали нельзя")

    def __repr__(self) -> str:
        try:
            return f"<P: активны параметры, прочитано {len(active_params().reads)}>"
        except NoParamsError:
            return "<P: вне сборки>"


#: Точка доступа к параметрам в скриптах деталей.
P = _ParamsFacade()


# ------------------------------------------------------------ помощники lib/
#: Имя, под которым скрипты проекта импортируют свои помощники:
#: `from lib.gears import involute`. Так было устроено в рабочем процессе CAD
#: (`work/lib/`), и порт скриптов оттуда должен быть механическим.
PROJECT_LIB = "lib"


class FreshSourceLoader(__import__("importlib.machinery").machinery.SourceFileLoader):
    """Загрузчик кода проекта: всегда из исходника, без `.pyc`.

    Кеш байткода Python сверяет исходник по времени изменения С ТОЧНОСТЬЮ ДО
    СЕКУНДЫ и по размеру. Правка «1 → 3» в ту же секунду размер не меняет, и
    загружается УСТАРЕВШИЙ байткод — сборка идёт по вчерашнему коду, хотя файл
    уже другой. Для скриптов, которые агент правит по одной цифре, это не
    теоретическая беда: так тест кеша и поймал её. Компиляция скрипта детали
    стоит доли миллисекунды, и экономить на ней незачем. Заодно в каталоге
    проекта не появляется `__pycache__`.
    """

    def get_code(self, fullname):
        source = self.get_data(self.get_filename(fullname))
        return compile(source, self.get_filename(fullname), "exec", dont_inherit=True)

    def set_data(self, path, data, *, _mode=0o666):     # pragma: no cover
        return None                                    # .pyc не пишем вовсе


def _fresh(spec):
    """Подменить загрузчик найденного модуля проекта на `FreshSourceLoader`."""
    origin = getattr(spec, "origin", None)
    if spec is not None and isinstance(origin, str) and origin.endswith(".py"):
        spec.loader = FreshSourceLoader(spec.name, origin)
    return spec


class _ProjectLibFinder:
    """Искатель модулей: `lib` — это каталог `lib/` ТЕКУЩЕГО проекта.

    Долго `lib/` существовал только в документации. Навык обещал, что
    «`parts/` и `lib/` подхватываются сами», перила объявляли его каталогом
    скриптов проекта — а `from lib.helpers import …` падало: каталог не
    лежал ни в каком пути поиска, и сами же перила запрещали имя `lib`.

    Корень берётся из `use_project`, а не из `sys.path`: так два проекта в одном
    процессе не подхватывают помощники друг друга, а постороннего пакета с
    именем `lib` искатель не касается — вне проекта он молчит. Подмодули
    (`lib.gears`) находит обычный механизм по `__path__` пакета.
    """

    def find_spec(self, fullname, path=None, target=None):
        from importlib.machinery import PathFinder
        from pathlib import Path

        if fullname.startswith(PROJECT_LIB + "."):
            # Подмодуль: ищется по `__path__` пакета, как обычно, но грузится
            # из исходника — см. FreshSourceLoader.
            return _fresh(PathFinder.find_spec(fullname, path)) if path else None
        if fullname != PROJECT_LIB:
            return None
        root = _ROOT.get()
        if root is None:
            return None
        if not (Path(str(root)) / PROJECT_LIB).is_dir():
            return None
        return _fresh(PathFinder.find_spec(fullname, [str(root)]))

    def invalidate_caches(self) -> None:          # pragma: no cover
        pass


def forget_project_lib() -> None:
    """Забыть загруженные помощники прежнего проекта.

    `sys.modules` кеширует модуль по имени, и без этого второй проект в том же
    процессе получил бы `lib` первого.
    """
    import sys

    for name in [n for n in sys.modules
                 if n == PROJECT_LIB or n.startswith(PROJECT_LIB + ".")]:
        del sys.modules[name]


def _install_finder() -> None:
    import sys

    if not any(isinstance(f, _ProjectLibFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _ProjectLibFinder())


_install_finder()
