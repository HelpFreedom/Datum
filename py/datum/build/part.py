"""Объявление детали и её сборка.

Деталь — это Python-функция, а не строка в дереве фич: корпус CAD показал, что
эвольвентные зубья, шевроны и решения треугольников в дерево не ложатся. Но
функция исполняется в подготовленной среде, где каждая геометрическая операция
попадает в журнал, а каждое обращение к параметру записывается.
"""

from __future__ import annotations

import functools

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from datum.build.context import use_params
from datum.doc.params import ParamRegistry
from datum.geom.guard import Expect, GuardError, GuardMode, checkpoint
from datum.geom.solid import Solid
from datum.geom.topology import TopologyReport

__all__ = ["PartDef", "BuildResult", "part", "build_part", "PARTS", "clear_parts",
           "source_digest", "project_code_digest", "code_digest", "describe_error",
           "PRINT_UP"]

#: Какая ось детали смотрит вверх на столе принтера. Словарь закрытый: шесть
#: направлений осей. Наклонной печати в корпусе не встретилось ни разу, а
#: свободная строка («плашмя», «на ребре») ничего не говорила проверкам — и одну
#: такую пометку однажды прочли неверно, объявив шибер перевёрнутым вопреки
#: записанному «на ребре».
PRINT_UP = ("+Z", "-Z", "+X", "-X", "+Y", "-Y")


def source_digest(pdef: "PartDef | None") -> str:
    """Отпечаток исходника детали.

    Нужен в двух местах: кеш сборок отличает «изменился код» от «изменились
    числа», а снимок версии по нему показывает, правился ли скрипт.
    """
    import hashlib
    import inspect

    if pdef is None:
        return ""
    try:
        src = inspect.getsource(pdef.fn)
    except (OSError, TypeError):                 # pragma: no cover
        return ""
    return hashlib.sha256(src.encode("utf-8")).hexdigest()[:16]

#: Пакеты Datum, через которые скрипт детали строит геометрию.
ENGINE_PACKAGES = ("geom", "std", "build")


#: Библиотеки, чья версия меняет форму детали (см. `engine_digest`).
GEOMETRY_LIBRARIES = ("manifold3d", "fonttools", "uharfbuzz")


@functools.lru_cache(maxsize=1)
def engine_digest() -> str:
    """Отпечаток кода Datum, строящего геометрию, и версий библиотек, от
    которых зависит форма: ядро manifold3d, контуры букв fontTools и набор
    надписи HarfBuzz (другая версия — другие пары кернинга).

    Один раз на процесс: исходники Datum во время работы не меняются, а
    сменились — это уже другой процесс (переустановка, новый запуск).
    """
    import hashlib
    from importlib.metadata import PackageNotFoundError, version

    import datum

    h = hashlib.sha256()
    for package in GEOMETRY_LIBRARIES:
        try:
            h.update(f"{package}={version(package)}".encode("ascii"))
        except PackageNotFoundError:             # pragma: no cover
            h.update(f"{package}=?".encode("ascii"))
    root = Path(datum.__file__).resolve().parent
    for package in ENGINE_PACKAGES:
        for f in sorted((root / package).rglob("*.py")):
            if "__pycache__" in f.parts:
                continue
            h.update(str(f.relative_to(root)).encode("utf-8") + b"\0" + f.read_bytes() + b"\0")
    # Встроенные шрифты — тоже код, строящий геометрию: надпись — их контуры.
    for f in sorted((root / "assets" / "fonts").glob("*")):
        if f.is_file():
            h.update(str(f.relative_to(root)).encode("utf-8") + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()[:16]


def project_code_digest(pdef: "PartDef | None", root: str | Path | None) -> str:
    """Отпечаток кода ПРОЕКТА, от которого зависит деталь: тело функции, файл
    детали целиком и все модули `lib/`.

    `source_digest` смотрит только на тело функции. Правка помощника в `lib/`
    или константы в начале того же файла меняет геометрию, а тело функции — нет:
    кеш молча отдавал вчерашний меш, а сравнение снимков не замечало правки.

    Правка любого помощника задевает все детали — грубо, но верно: кто из них
    что импортирует, выяснить можно лишь исполнив их. Кода Datum здесь нет —
    его добавляет `code_digest`: снимку важна правка человека или агента, а не
    обновление программы.
    """
    import hashlib
    import inspect

    base = source_digest(pdef)
    if not base or pdef is None:
        return ""
    h = hashlib.sha256(base.encode("ascii"))
    try:
        own = inspect.getsourcefile(pdef.fn)
    except TypeError:                            # pragma: no cover
        own = None
    files = [Path(own)] if own else []
    if root is not None:
        lib = Path(root) / "lib"
        if lib.is_dir():
            files += sorted(f for f in lib.rglob("*.py") if "__pycache__" not in f.parts)
    for f in files:
        try:
            data = f.read_bytes()
        except OSError:                          # pragma: no cover
            continue
        try:
            rel = str(f.resolve().relative_to(Path(root).resolve())) if root else f.name
        except ValueError:                       # файл детали вне проекта
            rel = f.name
        h.update(rel.encode("utf-8") + b"\0" + data + b"\0")
    return h.hexdigest()[:16]


def code_digest(pdef: "PartDef | None", root: str | Path | None) -> str:
    """Отпечаток ВСЕГО кода, от которого зависит деталь, — для ключа кеша:
    код проекта (`project_code_digest`) и код САМОГО Datum, которым скрипт
    строит геометрию (`engine_digest`). Последний — потому что после правки
    сглаживания узора в `geom/field.py` пример собрался «из кеша» со старой
    геометрией — ровно та беда, ради которой ключ и считается.
    """
    import hashlib

    own = project_code_digest(pdef, root)
    if not own:
        return ""
    return hashlib.sha256(f"{own}|{engine_digest()}".encode("ascii")).hexdigest()[:16]


#: Детали, объявленные в текущем процессе (заполняется декоратором).
PARTS: dict[str, "PartDef"] = {}


def clear_parts() -> None:
    PARTS.clear()


@dataclass(frozen=True, slots=True)
class PartDef:
    name: str
    fn: Callable[..., Solid]
    color: str = "#9aa4b2"
    print_up: str = ""                   # "" — не объявлена; иначе одна из PRINT_UP
    expect: Expect = field(default_factory=Expect)
    doc: str = ""
    zones: Callable[[], dict] | None = None
    printed: bool = True
    #: Настройки печати детали (`datum.fab.settings`): стенки, заполнение…
    print_cfg: dict | None = None


def part(name: str, *, color: str = "#9aa4b2", print_up: str = "",
         expect: Expect | None = None,
         zones: Callable[[], dict] | None = None,
         printed: bool = True,
         print: dict | None = None,                 # noqa: A002 — так его пишет скрипт
         ) -> Callable[[Callable[..., Solid]], Callable[..., Solid]]:
    """Объявить деталь. Возвращает исходную функцию — её можно звать и напрямую.

    `print_up` — ось детали, которая на столе смотрит вверх. Деталь строится в
    СБОРОЧНОМ положении, а печатается в своём; проверки печатности берут
    ориентацию отсюда, если в проверке она не задана.

    `zones` — именованные области детали, которые проверки могут ИСКЛЮЧИТЬ по
    имени (`exclude_zones: [язычок]` у `min_wall`, `overhang`): упругий
    язычок тоньше стенки нарочно. Функция без аргументов, возвращающая
    `{имя: тело}` в координатах скрипта; зовётся при проверке, с параметрами
    (`P.…`), и ставится туда же, куда деталь (`placement.yaml`). Функция, а не
    коробки сразу: при объявлении параметров ещё нет, а язычок от них зависит.
    Берите зону С ЗАПАСОМ вокруг элемента (на миллиметр): `overhang`
    исключает грани по их центрам, и грань ровно на границе зоны может
    оказаться и внутри, и снаружи.

    `printed=False` — деталь НЕ ПЕЧАТАЕТСЯ: покупная вещь (плата, мотор,
    подшипник) или показная сборка целиком. Она собирается, видна в окне и
    участвует в проверках как любая (`"@плата"` в checks.yaml), но `export` и
    `slice` её пропускают, базовых проверок печатности у неё нет и о краю
    стола она не предупреждает. Прежде такую деталь выгружали вместе с
    печатными, а базовые проверки краснели на ней (Gear Cube: статуэтка
    целиком — «три запаянные полости»).

    `print={"walls": 3, "infill": 0.2, …}` — НАСТРОЙКИ ПЕЧАТИ детали поверх
    `[print]` из datum.toml и профиля слайсера: по ним считается масса
    (`datum.fab`, для симуляции) и они же уходят в слайсер при `slice`.
    Значение — число или имя параметра (`"INFILL_GEAR"`): тогда развёртка
    гоняет и его. Ключи — `datum.fab.settings.SETTING_KEYS`.
    """
    if print is not None:
        from datum.fab.settings import SETTING_KEYS

        if not isinstance(print, dict):
            raise ValueError(f"деталь {name!r}: print — словарь {{'walls': 3, 'infill': 0.2}}")
        unknown = sorted(set(print) - set(SETTING_KEYS))
        if unknown:
            raise ValueError(f"деталь {name!r}: непонятные настройки печати {unknown}; известны: "
                             + ", ".join(SETTING_KEYS))
    if not isinstance(printed, bool):
        raise ValueError(f"деталь {name!r}: printed={printed!r} — ожидается True или False")
    if print_up and print_up not in PRINT_UP:
        raise ValueError(f"деталь {name!r}: print_up={print_up!r} — ожидается одна из "
                         f"{', '.join(PRINT_UP)} (ось детали, смотрящая вверх на столе)")
    if zones is not None and not callable(zones):
        raise ValueError(f"деталь {name!r}: zones — функция без аргументов, возвращающая "
                         f"{{имя: тело}}, например zones=lambda: {{'язычок': box(...)}}")

    def wrap(fn: Callable[..., Solid]) -> Callable[..., Solid]:
        # Две детали с одним именем: раньше молча побеждала последняя
        # загруженная, и первую было не найти ни в одном списке.
        if name in PARTS and PARTS[name].fn is not fn:
            import inspect

            def where(f) -> str:
                try:
                    return Path(inspect.getsourcefile(f) or "?").name
                except TypeError:                # pragma: no cover
                    return "?"

            raise ValueError(f"деталь {name!r} объявлена дважды: в "
                             f"{where(PARTS[name].fn)} и в {where(fn)}")
        PARTS[name] = PartDef(name=name, fn=fn, color=color, print_up=print_up,
                              expect=expect or Expect(), doc=(fn.__doc__ or "").strip(),
                              zones=zones, printed=printed,
                              print_cfg=dict(print) if print is not None else None)
        return fn

    return wrap


#: У части встроенных исключений `str()` пуст, и сообщение «СБОЙ деталь:»
#: получается без объяснения. Для них текст задаётся здесь.
_SILENT_ERRORS = {
    "MemoryError": "не хватило памяти при сборке детали",
    "RecursionError": "слишком глубокая рекурсия в скрипте детали",
    "KeyboardInterrupt": "сборка прервана",
    "SystemExit": "скрипт детали завершил процесс",
}


def describe_error(err: BaseException | None) -> str:
    """Человеческое описание сбоя сборки."""
    if err is None:
        return ""
    name = type(err).__name__
    text = str(err).strip()
    if text:
        if isinstance(err, GuardError):
            return text
        from datum.build.hints import with_hint     # лениво: подсказкам нужен весь datum.geom

        return with_hint(f"{name}: {text}", err)
    return _SILENT_ERRORS.get(name, f"{name} без пояснения")


@dataclass(frozen=True, slots=True)
class BuildResult:
    name: str
    solid: Solid | None
    topology: TopologyReport | None
    depends_on: tuple[str, ...]
    ms: float
    error: GuardError | Exception | None = None
    ops: int = 0
    cached: bool = False
    draft: bool = False          # черновая сборка (`draft()` в скрипте)
    note: str = ""               # что стоит знать сверх итога (негодная запись кеша)

    @property
    def ok(self) -> bool:
        return self.error is None and self.solid is not None

    def as_dict(self) -> dict:
        d: dict[str, Any] = {
            "name": self.name, "ok": self.ok, "ms": round(self.ms, 1),
            "ops": self.ops, "cached": self.cached,
            "depends_on": list(self.depends_on),
        }
        if self.draft:
            d["draft"] = True
        if self.note:
            d["note"] = self.note
        if self.topology:
            d["topology"] = self.topology.as_dict()
        if self.error is not None:
            d["error"] = (self.error.as_dict() if isinstance(self.error, GuardError)
                          else {"reason": type(self.error).__name__,
                                "message": describe_error(self.error)})
        return d

    def line(self) -> str:
        if self.ok:
            t = self.topology
            tail = "из кеша" if self.cached else f"{self.ms:.0f} мс"
            if self.draft:
                tail += ", ЧЕРНОВИК"
            if self.note:
                tail += f" ({self.note})"
            return (f"  OK   {self.name}: объём {t.volume:.1f} мм³, тел {t.bodies}, "
                    f"граней {t.triangles}, {tail}")
        return f"  СБОЙ {self.name}: {describe_error(self.error)}"


def build_part(pdef: PartDef, registry: ParamRegistry, *,
               overrides: dict[str, float] | None = None,
               mode: GuardMode | str = GuardMode.CHECKPOINT,
               frame=None,
               **kwargs) -> BuildResult:
    """Собрать деталь. Страж вызывается ровно один раз — в контрольной точке.

    `frame` — как деталь стоит в проекте (перенос мышью, `placement.yaml`).
    Страж смотрит тело в ЭТОМ положении, чтобы место полости в отказе было
    там же, где деталь в окне и в `section`; возвращается же деталь, какой её
    отдал скрипт: кеш хранит её без положения. Топология от жёсткого
    движения не меняется — меняются только координаты в сообщении.
    """
    proxy = registry.proxy(overrides)
    t0 = time.perf_counter()
    solid: Solid | None = None
    topo: TopologyReport | None = None
    err: Exception | None = None
    try:
        with use_params(proxy):
            solid = pdef.fn(**kwargs)
        if not isinstance(solid, Solid):
            raise TypeError(
                f"деталь {pdef.name!r} должна вернуть Solid, получено "
                f"{type(solid).__name__}. Если собираете из Manifold — оберните "
                f"через Solid.wrap")
        solid = solid.renamed(pdef.name) if not solid.name else solid
        topo = checkpoint(frame(solid) if frame is not None else solid,
                          pdef.expect, mode=mode)
    except Exception as e:
        err = e
        # Важно: при сбое `solid` может держать мусор, который вернула функция
        # (например, число). Дальше по нему считается журнал операций, поэтому
        # оставляем только настоящее тело.
        if not isinstance(solid, Solid):
            solid = None

    deps = set(proxy.reads)
    for name in list(deps):
        deps |= registry.depends_on(name)

    ops = len(solid.journal) if isinstance(solid, Solid) else 0
    return BuildResult(
        name=pdef.name, solid=None if err else solid, topology=topo,
        depends_on=tuple(sorted(deps)), ms=(time.perf_counter() - t0) * 1000,
        error=err, ops=ops)
