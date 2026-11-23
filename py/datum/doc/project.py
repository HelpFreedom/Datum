"""Проект Datum на диске.

    <проект>/
      datum.toml            имя, единицы, принтер, режим стража
      params/base.yaml      реестр параметров
      parts/*.py            скрипты деталей (@part)
      checks/checks.yaml    декларативные проверки из каталога
      checks/custom.py      проектные проверки на Python (@check)
      output/               экспорт
      .datum/               кеш, журналы (в .gitignore)

Скрипты деталей исполняются, потому что деталь — это программа: эвольвентные
зубья и решения треугольников в дерево фич не ложатся. Исполнение идёт в
подготовленной среде: параметры доступны только через прокси, каждая операция
попадает в журнал, страж вызывается в контрольной точке.
"""

from __future__ import annotations

import importlib.util
import sys
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from datum.build.context import (FreshSourceLoader, forget_project_lib, use_params,
                                  use_project)
from datum.build.part import PARTS, BuildResult, PartDef, build_part, clear_parts
from datum.checks.library import LIBRARY, PartFnRef, PartRef, resolve_args
from datum.doc.assembly import Assembly, AssemblyError
from datum.checks.result import CheckResult, CheckRun
from datum.checks.runner import CheckSpec, Sensitivity, run_checks, sweep_assumed
from datum.doc.params import ParamProxy, ParamRegistry
from datum.geom.guard import GuardMode
from datum.geom.solid import Solid

__all__ = ["Project", "ProjectError", "DEFAULT_MANIFEST"]


class ProjectError(RuntimeError):
    """Проект не открылся или описан неверно."""


DEFAULT_MANIFEST: dict[str, Any] = {
    "schema": 1,
    "name": "безымянный",
    "units": "mm",
    "printer": {"profile": "elegoo-cc2-0.4", "bed": [256, 256, 256],
                "nozzle": 0.4, "layer": 0.20, "material": "PLA"},
    "build": {"guard": "checkpoint"},
    "cache": {"enabled": True, "budget_mb": 2048, "verify": False},
}


def _validate_manifest(manifest: dict) -> None:
    """Проверить datum.toml при открытии, а не там, где значение понадобится.

    Раньше `guard = "bogus"` ронял `datum info` голым ValueError без слова о
    файле, а `datum check` — проходил: путь проверок режима не читал вовсе.
    """
    def bad(key: str, why: str) -> ProjectError:
        return ProjectError(f"datum.toml: {key} — {why}")

    build = manifest.get("build", {})
    if not isinstance(build, dict):
        raise bad("[build]", "ожидалась таблица")
    guard = build.get("guard", "checkpoint")
    valid = [m.value for m in GuardMode]
    if guard not in valid:
        raise bad("build.guard", f"{guard!r}, а допустимо: {', '.join(valid)}")

    printer = manifest.get("printer", {})
    if not isinstance(printer, dict):
        raise bad("[printer]", "ожидалась таблица")
    bed = printer.get("bed", [256, 256, 256])
    if (not isinstance(bed, (list, tuple)) or len(bed) != 3
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
                       for v in bed)):
        raise bad("printer.bed", f"{bed!r}, а нужны три положительных числа, мм")
    for key in ("nozzle", "layer"):
        if key in printer:
            v = printer[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not v > 0:
                raise bad(f"printer.{key}", f"{v!r}, а нужно положительное число, мм")

    printing = manifest.get("print", {})
    if not isinstance(printing, dict):
        raise bad("[print]", "ожидалась таблица настроек печати (walls, infill, …)")
    from datum.fab.settings import SETTING_KEYS

    unknown = sorted(set(printing) - set(SETTING_KEYS))
    if unknown:
        raise bad("[print]", f"непонятные ключи {unknown}; известны: {', '.join(SETTING_KEYS)}")


def _load_module(path: Path, name: str):
    # Из исходника, без .pyc: иначе правка в ту же секунду без смены размера
    # загружала бы прежний код (см. FreshSourceLoader).
    spec = importlib.util.spec_from_file_location(
        name, path, loader=FreshSourceLoader(name, str(path)))
    if spec is None or spec.loader is None:             # pragma: no cover
        raise ProjectError(f"не загрузить модуль {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        from datum.build.hints import with_hint

        raise ProjectError(with_hint(f"{path.name}: {type(e).__name__}: {e}", e)) from e
    return module


def _accepts(entry) -> set[str]:
    """Имена аргументов проверки каталога."""
    import inspect

    return set(inspect.signature(entry.fn).parameters)


def _result_selected(result, only: list[str], parts: list[str]) -> bool:
    """Результат подходит под фильтр прогона (`Project.run_checks`)."""
    import fnmatch

    if only and not any(fnmatch.fnmatchcase(result.id, pat) or result.id == pat
                        or result.id.startswith(pat + ".") for pat in only):
        return False
    if parts and result.inputs and not (set(result.inputs) & set(parts)):
        return False
    return True


@dataclass
class Project:
    root: Path
    manifest: dict
    params: ParamRegistry
    parts: dict[str, PartDef] = field(default_factory=dict)
    check_decls: list[dict] = field(default_factory=list)
    custom_specs: list[CheckSpec] = field(default_factory=list)
    #: порядок сборки; из него выводятся проверки собираемости
    assembly: Assembly = field(default_factory=Assembly)
    #: кеш проверок в пределах прогона: (деталь, отпечаток, аргументы) → (тело, чтения)
    _cache: dict = field(default_factory=dict, repr=False)
    #: кеш сборок на диске; создаётся лениво
    _disk: object | None = field(default=None, repr=False, compare=False)
    #: положение деталей на столе (placement.yaml) — поверх того, что вернул скрипт
    placements: dict = field(default_factory=dict)
    #: какой файл parts/*.py объявил деталь — удаление уводит в корзину файл
    part_files: dict = field(default_factory=dict)
    #: центр габарита детали, какой её вернул скрипт, — ось поворота; по сборке
    _centres: dict = field(default_factory=dict, repr=False, compare=False)
    # Центр детали, собранной в прогоне проверок, по (имя, значения): вокруг
    # него поворачиваются её зоны (`zones`).
    _run_centres: dict = field(default_factory=dict, repr=False, compare=False)
    #: Сколько проверок последнего прогона взято из кеша: (взято, всего).
    check_cache_use: tuple = field(default=(0, 0), repr=False, compare=False)
    #: механизмы (`@mechanism` в parts/*.py) и файлы, где они объявлены
    mechanisms: dict = field(default_factory=dict)
    mechanism_files: dict = field(default_factory=dict)
    #: результаты симуляции в пределах прогона: (механизм, сценарий, значения) → SimResult
    _sim: dict = field(default_factory=dict, repr=False, compare=False)

    # ---------------------------------------------------------------- открытие
    @staticmethod
    def open(path: str | Path) -> "Project":
        root = Path(path).resolve()
        if not root.is_dir():
            raise ProjectError(f"нет каталога проекта: {root}")

        manifest = dict(DEFAULT_MANIFEST)
        toml = root / "datum.toml"
        if toml.is_file():
            try:
                manifest.update(tomllib.loads(toml.read_text(encoding="utf-8")))
            except tomllib.TOMLDecodeError as e:
                raise ProjectError(f"datum.toml: {e}") from e
        elif not any((root / sub).exists() for sub in ("parts", "checks", "params")):
            # Пустой или чужой каталог. Раньше он открывался как «проект
            # безымянный» без деталей, и `datum check` отвечал «ИТОГ: OK».
            raise ProjectError(
                f"{root} — не проект Datum: нет ни datum.toml, ни parts/, ни "
                f"checks/, ни params/. Новый проект: `datum init {root}`")
        _validate_manifest(manifest)

        params_file = root / "params" / "base.yaml"
        params = ParamRegistry.load(params_file) if params_file.is_file() else ParamRegistry()
        # Свойства пластика из каталога — параметрами MAT_* с происхождением,
        # если проект не объявил своих (doc/materials.py).
        from datum.doc.materials import material_params

        params = params.with_defaults(
            material_params(str(manifest.get("printer", {}).get("material", "PLA"))))

        # Скрипты деталей регистрируются глобально, поэтому набор чистится перед
        # загрузкой и снимается сразу после — иначе проекты перетекали бы друг в друга.
        # То же с помощниками из lib/: модуль `lib` кешируется по имени.
        from datum.build.mechanism import MECHANISMS, clear_mechanisms

        clear_parts()
        clear_mechanisms()
        forget_project_lib()
        parts_dir = root / "parts"
        bed = tuple(float(v) for v in manifest.get("printer", {}).get("bed", [256, 256, 256]))
        part_files: dict[str, Path] = {}
        mechanism_files: dict[str, Path] = {}
        with use_project(root, bed=bed):
            if parts_dir.is_dir():
                for f in sorted(parts_dir.glob("*.py")):
                    if f.name.startswith("_"):
                        continue
                    before = set(PARTS)
                    before_m = set(MECHANISMS)
                    _load_module(f, f"datum_project_{root.name}_parts_{f.stem}")
                    for name in set(PARTS) - before:
                        part_files[name] = f
                    for name in set(MECHANISMS) - before_m:
                        mechanism_files[name] = f
            parts = dict(PARTS)
            mechanisms = dict(MECHANISMS)
        clear_parts()
        clear_mechanisms()

        from datum.doc import placement as _placement

        try:
            placements = _placement.load(root)
        except _placement.PlacementError as e:
            raise ProjectError(str(e)) from e
        stray = sorted(set(placements) - set(parts))
        if stray:
            # Как и с порядком сборки: опечатка в имени — это положение, молча
            # не применённое, а на экране деталь стояла бы не там.
            from datum.build.hints import names_hint

            tip = names_hint(stray, parts)
            raise ProjectError(f"{_placement.FILE}: нет таких деталей: {stray}; "
                               f"есть: {sorted(parts)}" + (f". Подсказка: {tip}" if tip else ""))

        decls: list[dict] = []
        checks_yaml = root / "checks" / "checks.yaml"
        if checks_yaml.is_file():
            text = checks_yaml.read_text(encoding="utf-8")
            try:
                raw = yaml.safe_load(text) or []
            except yaml.YAMLError as e:
                hint = (" Похоже на ссылку ${…} без кавычек: в YAML её надо брать в "
                        "кавычки — \"${params.GAP}\"." if "${" in text else "")
                raise ProjectError(f"checks/checks.yaml не разобран: {e}.{hint}") from e
            if not isinstance(raw, list):
                raise ProjectError("checks/checks.yaml должен быть списком описаний")
            decls = raw

        assembly_yaml = root / "assembly.yaml"
        assembly = Assembly()
        if assembly_yaml.is_file():
            try:
                assembly = Assembly.load(assembly_yaml)
            except AssemblyError as e:
                raise ProjectError(str(e)) from e
            # Имя в порядке сборки обязано быть настоящей деталью: опечатка
            # здесь стоила бы пропущенной проверки, а это ровно та беда, ради
            # которой порядок и записывается.
            missing = assembly.missing(parts)
            if missing:
                from datum.build.hints import names_hint

                tip = names_hint(missing, parts)
                raise ProjectError(
                    f"assembly.yaml: в порядке сборки нет таких деталей: {missing}; "
                    f"есть: {sorted(parts)}" + (f". Подсказка: {tip}" if tip else ""))

        custom: list[CheckSpec] = []
        custom_py = root / "checks" / "custom.py"
        if custom_py.is_file():
            module = _load_module(custom_py, f"datum_project_{root.name}_checks_custom")
            for name in dir(module):
                fn = getattr(module, name)
                if callable(fn) and getattr(fn, "_datum_check_id", None):
                    custom.append(CheckSpec(id=fn._datum_check_id, fn=fn,
                                            title=getattr(fn, "_datum_check_title", ""),
                                            group=getattr(fn, "_datum_check_group", "custom")))
        # Проектной проверке может понадобиться геометрия, а не только числа.
        # Как — решает она сама своей сигнатурой, см. `_wrap_custom`.

        project = Project(root=root, manifest=manifest, params=params, parts=parts,
                          check_decls=decls, custom_specs=custom, assembly=assembly,
                          placements=placements, part_files=part_files,
                          mechanisms=mechanisms, mechanism_files=mechanism_files)
        project.custom_specs = [project._wrap_custom(spec) for spec in custom]
        project._validate_checks()
        return project

    def _validate_checks(self) -> None:
        """Разобрать все описания проверок при открытии, а не при первом прогоне.

        Раньше `info` считал проверки по сырому YAML и отвечал «проверок: 2», а
        `check` на том же проекте падал: неизвестная проверка, неверная
        важность или спор ориентаций всплывали только в прогоне. Построение
        описания ничего не считает, поэтому дёшево.

        И — ДУБЛИ. Две проверки с одним id раньше обе исполнялись, а в сравнении
        версий схлопывались в одну: сломавшаяся пряталась за одноимённой
        прошедшей, и регрессия была невидима.
        """
        seen: dict[str, str] = {}

        def claim(check_id: str, where: str) -> None:
            if check_id in seen:
                raise ProjectError(
                    f"проверка {check_id!r} объявлена дважды ({seen[check_id]} и {where}). "
                    f"Дайте им разные id — иначе в сравнении версий одна спрячет другую")
            seen[check_id] = where

        for i, decl in enumerate(self.check_decls, 1):
            if not isinstance(decl, dict):
                raise ProjectError(f"checks/checks.yaml, описание №{i}: ожидался словарь "
                                   f"с полями id/use/args, получено {decl!r}")
            self._spec_from_decl(decl)
            claim(str(decl.get("id") or decl.get("use")), f"checks.yaml №{i}")
        for spec in self.custom_specs:
            claim(spec.id, "checks/custom.py")
        for decl in self.derived_decls():
            self._spec_from_decl(decl)
            if decl["id"] in seen and seen[decl["id"]] == "checks/custom.py":
                claim(decl["id"], "assembly.yaml")

    # ---------------------------------------------------------------- свойства
    @property
    def name(self) -> str:
        return str(self.manifest.get("name", self.root.name))

    @property
    def guard_mode(self) -> GuardMode:
        return GuardMode(self.manifest.get("build", {}).get("guard", "checkpoint"))

    @property
    def cache_config(self) -> dict:
        cfg = dict(DEFAULT_MANIFEST["cache"])
        cfg.update(self.manifest.get("cache", {}) or {})
        return cfg

    @property
    def cache(self):
        """Кеш сборок на диске. Создаётся при первом обращении."""
        from datum.build.cache import BuildCache

        if self._disk is None:
            cfg = self.cache_config
            self._disk = BuildCache(self.root / ".datum" / "cache",
                                    budget_mb=int(cfg.get("budget_mb", 2048)))
        return self._disk

    @property
    def bed(self) -> tuple[float, float, float]:
        b = self.manifest.get("printer", {}).get("bed", [256, 256, 256])
        return (float(b[0]), float(b[1]), float(b[2]))

    def _context(self, *, draft: bool = False):
        """Контекст сборки детали: корень проекта, стол принтера, черновик.

        Один на все пути — сборку, кеш с перепроверкой, проверки. Когда входов
        было несколько и каждый собирал контекст сам, в одном из них его не
        оказалось вовсе, и `imported("…")` падал только в проверках.
        Черновик бывает только у `build(draft=True)`: проверки собирают сами.
        """
        return use_project(self.root, bed=self.bed, draft=draft)

    # ---------------------------------------------------------------- сборка
    def build(self, names: list[str] | None = None,
              overrides: dict[str, float] | None = None, *,
              use_cache: bool | None = None, draft: bool = False,
              on_built=None) -> dict[str, BuildResult]:
        """`on_built(имя, результат, готово, всего)` зовётся после КАЖДОЙ детали —
        так сборка показывает детали по мере готовности, не дожидаясь последней."""
        targets = names or sorted(self.parts)
        missing = [n for n in targets if n not in self.parts]
        if missing:
            from datum.build.hints import names_hint

            tip = names_hint(missing, self.parts)
            raise ProjectError(f"нет таких деталей: {missing}; есть: {sorted(self.parts)}"
                               + (f". Подсказка: {tip}" if tip else ""))

        cfg = self.cache_config
        enabled = cfg.get("enabled", True) if use_cache is None else use_cache
        if not enabled:
            with self._context(draft=draft):
                return {n: self._drafted(self._placed(build_part(
                            self.parts[n], self.params, overrides=overrides,
                            mode=self.guard_mode, frame=self._frame(n))), draft)
                        for n in targets}

        from datum.build.imported import imported_stamp
        from datum.build.part import code_digest
        from datum.build.worker import stage
        from datum.geom.text import project_fonts_stamp

        values = self.params.resolve(overrides)
        cache = self.cache
        verify = bool(cfg.get("verify", False))
        # Чужие модели входят в ключ кеша: подменили файл — прежняя запись
        # становится негодной. Без этого кеш молча показывал бы вчерашнюю
        # геометрию, а это ровно тот род ошибки, ради которого Datum и затеян.
        stamp = imported_stamp(self.root)
        # Шрифты проекта — как модели imported/: заменили файл — надписи
        # пересобираются, а не встают из кеша прежними буквами.
        fonts = project_fonts_stamp(self.root)
        # Свои файлы данных (`data/`) — так же: скрипт детали читает таблицу, и
        # её правка обязана пересобрать деталь.
        from datum.build.imported import data_stamp

        data = data_stamp(self.root)
        # Стол — тоже: деталь, поставленная по `bed()` в центр, после смены
        # стола в datum.toml из кеша встала бы на старое место.
        table = "x".join(f"{v:g}" for v in self.bed)
        out: dict[str, BuildResult] = {}
        stored_any = False
        for n in targets:
            src = code_digest(self.parts[n], self.root)
            if src:
                src = f"{src}+bed:{table}"
            if src and stamp:
                src = f"{src}+imported:{stamp}"
            if src and fonts:
                src = f"{src}+fonts:{fonts}"
            if src and data:
                src = f"{src}+data:{data}"
            # Черновик — своя запись: иначе проверка взяла бы грубую деталь.
            if src and draft:
                src = f"{src}+draft"
            hit = cache.lookup(src, values) if src else None
            rejected = ""
            if hit is not None:
                res, rejected = self._result_from_cache(n, hit, verify=verify,
                                                        overrides=overrides, draft=draft)
                if res is not None:
                    out[n] = self._drafted(self._placed(res), draft)
                    if on_built is not None:
                        on_built(n, out[n], len(out), len(targets))
                    continue
            with self._context(draft=draft), \
                    stage(f"сборка детали {n}" + (" (черновик)" if draft else "")):
                res = build_part(self.parts[n], self.params, overrides=overrides,
                                 mode=self.guard_mode, frame=self._frame(n))
            # В кеш — деталь, какой её вернул скрипт; положение — поверх.
            if res.ok and src:
                reads = {k: values[k] for k in res.depends_on if k in values}
                kept, stored = self._store_checked(n, src, reads, res)
                stored_any = stored_any or not kept
                if stored is not None:
                    # Дальше идёт РОВНО ТО тело, что записано в кеш и показано
                    # в окне (сетка во float32), а не исходное в двойной
                    # точности: иначе первый прогон проверок после правки мерил
                    # одно тело, а все следующие — другое, и числа гуляли в
                    # шестом знаке («зацеп 89.8000 → 89.80007») — сравнение
                    # версий и кеш проверок видели перемену там, где её нет.
                    res = replace(res, solid=stored)
                note = "; ".join(x for x in (
                    f"запись кеша была негодной: {rejected}" if rejected else "", kept) if x)
                if note:
                    res = replace(res, note=note)
            out[n] = self._drafted(self._placed(res), draft)
            if on_built is not None:
                on_built(n, out[n], len(out), len(targets))
        # Кеш держит бюджет сам: прежде чистила его только операция `cache_gc`,
        # которую никто не звал, и проекты копили по гигабайту (аудит 2026-11-21).
        # Только после записи нового и только когда бюджет превышен — один
        # запрос к индексу. Свежезаписанное уцелеет: уходит давно не читанное.
        if stored_any and cache.over_budget():
            cache.gc()
        return out

    def _store_checked(self, name: str, src: str, reads: dict,
                       res: BuildResult) -> tuple[str, Solid | None]:
        """Положить деталь в кеш вместе с тем, какой она ПРОЧТЁТСЯ обратно.

        Тело хранится сеткой во float32, и у составной детали (`compose`
        касающихся узлов) число тел и полостей после записи другое: вершины,
        совпадавшие в double, расходятся, и склеенное распадается. Сверка при
        чтении сравнивала прочитанное с числами СВЕЖЕЙ сборки — запись
        отвергалась молча, и «сборка» Gear Cube (339 тыс. треугольников)
        пересобиралась 24 с при каждом открытии проекта. Теперь рядом с
        метриками сборки (их видит человек) лежит разбор самих записанных
        байтов (`stored`) — с ним и сверяется чтение.

        Возвращает (причина, по которой запись не сделана, или "";
        записанное тело — каким оно читается обратно, или None).
        """
        from datum.build.cache import solid_from_bytes, solid_to_bytes
        from datum.geom.topology import analyze

        metrics = res.topology.as_dict(round_to=None)
        try:
            data = solid_to_bytes(res.solid)
            stored = solid_from_bytes(data, name=res.solid.name or name)
            back = analyze(stored.manifold, sliver_mm3=self.parts[name].expect.sliver_mm3)
        except Exception as e:                       # сетка не читается обратно
            return f"в кеш не записана: сетка не читается обратно ({type(e).__name__})", None
        if back.is_empty or back.status != "NoError":
            return f"в кеш не записана: после записи тело негодно ({back.status})", None
        metrics["stored"] = {"bodies": back.bodies, "sealed_voids": back.sealed_voids,
                             "triangles": back.triangles}
        self.cache.store(name, src, reads, {}, res.solid, metrics, data=data)
        return "", stored

    @staticmethod
    def _drafted(res: BuildResult, draft: bool) -> BuildResult:
        from dataclasses import replace

        return replace(res, draft=True) if draft else res

    def _frame(self, name: str):
        """Положение детали функцией для стража (`build_part(frame=…)`), или None."""
        from datum.doc import placement as _placement

        where = self.placements.get(name)
        if where is None or where.identity:
            return None
        return lambda solid: _placement.apply(solid, where)

    def _placed(self, res: BuildResult) -> BuildResult:
        """Поставить собранную деталь по placement.yaml; запомнить ось поворота."""
        from dataclasses import replace

        from datum.doc import placement as _placement

        if not res.ok:
            return res
        self._centres[res.name] = _placement.centre_of(res.solid)
        where = self.placements.get(res.name)
        if where is None or where.identity:
            return res
        return replace(res, solid=_placement.apply(res.solid, where))

    def placement_of(self, name: str) -> dict:
        """Положение детали для окна: сдвиг, углы и ось поворота (после сборки)."""
        from datum.doc.placement import Placement

        where = self.placements.get(name) or Placement()
        return {**where.as_dict(),
                "centre": [round(v, 4) for v in self._centres.get(name, (0.0, 0.0, 0.0))]}

    def _result_from_cache(self, name: str, hit, *, verify: bool,
                           overrides: dict[str, float] | None,
                           draft: bool = False) -> tuple[BuildResult | None, str]:
        """Собрать результат из кеша, перепроверив топологию.

        Разбор топологии на уже посчитанном теле стоит сотые доли миллисекунды,
        поэтому повторяется всегда: так испорченная или устаревшая запись не
        проскочит мимо ожиданий детали.

        Возвращает (результат, "") или (None, почему запись негодна) — причина
        идёт в строку сборки: молчаливый отказ кеша выглядел как «проект
        почему-то долго открывается».
        """
        from datum.geom.guard import GuardError, checkpoint
        from datum.geom.topology import analyze

        pdef = self.parts[name]
        stored = hit.metrics or {}
        try:
            # Любая беда чтения — запись негодна: урезанный после обрыва
            # питания файл давал ValueError, который не ловился, и деталь не
            # собиралась вовсе до ручной чистки кеша.
            solid = hit.solid(self.cache)
            if solid is None:
                return None, "объект вычищен"
            written = stored.get("stored")
            if isinstance(written, dict):
                # Запись знает, какой она читается (`_store_checked`): сверяем
                # с этим, а ожидания детали проверены свежей сборкой — код
                # детали, а с ним и её Expect, входит в ключ.
                topo = analyze(solid.manifold, sliver_mm3=pdef.expect.sliver_mm3)
                if (topo.is_empty or topo.status != "NoError"
                        or written.get("bodies") != topo.bodies
                        or written.get("sealed_voids") != topo.sealed_voids
                        or written.get("triangles") != topo.triangles):
                    return None, (f"записано тел {written.get('bodies')}, полостей "
                                  f"{written.get('sealed_voids')}, читается {topo.bodies} "
                                  f"и {topo.sealed_voids}")
            else:
                topo = checkpoint(solid, pdef.expect, mode=self.guard_mode)
                # Запись прежнего вида: метрики свежей сборки обязаны сходиться
                # с телом, иначе записи веры нет.
                if (stored.get("bodies") != topo.bodies
                        or stored.get("sealed_voids") != topo.sealed_voids):
                    return None, (f"записано тел {stored.get('bodies')}, полостей "
                                  f"{stored.get('sealed_voids')}, читается {topo.bodies} "
                                  f"и {topo.sealed_voids}")
        except GuardError as e:
            return None, f"страж: {e.reason}"
        except Exception as e:
            return None, f"не читается ({type(e).__name__})"

        if verify:
            # Пересборка — как без кеша: в положении детали (место полости в
            # отказе стража — там, где деталь стоит). Совпала — ответ ИЗ КЕША,
            # с сохранёнными метриками, как и без сверки: свежие числа в шестом
            # знаке другие (см. ниже), и включённая сверка меняла бы снимки.
            with self._context(draft=draft):
                fresh = build_part(pdef, self.params, overrides=overrides,
                                   mode=self.guard_mode, frame=self._frame(name))
            if not fresh.ok:
                return fresh, ""
            # Сравниваются ФОРМЫ (`Solid.digest`, канонический вид), а не
            # `hit.digest` — тот отпечаток байтов файла сетки, и с формой он не
            # совпадал никогда: сверка объявляла недетерминированной любую деталь.
            kept, made = solid.digest(), fresh.solid.digest()
            if kept != made:
                raise ProjectError(
                    f"деталь {name!r} собирается по-разному при одних и тех же "
                    f"параметрах: кеш даёт {kept[:12]}, пересборка "
                    f"{made[:12]}. Скрипт детали обязан быть "
                    f"детерминирован — уберите случайность, время и чтение "
                    f"внешних файлов")

        # Отчитываемся СОХРАНЁННЫМИ метриками, а не пересчитанными. Причина:
        # manifold считает объём во внутренней двойной точности, а тело из кеша
        # восстановлено из float32-вершин, и пересчёт даёт расхождение в шестом
        # знаке (12145.92 против 12145.9198). Само по себе это шум, но он
        # просачивается в снимок и даёт ложную строку «масса изменилась» при
        # сравнении версий. Кеш не должен менять числа.
        from datum.geom.topology import TopologyReport

        reported = TopologyReport.from_dict(stored) if stored else topo
        return BuildResult(name=name, solid=solid, topology=reported,
                           depends_on=tuple(sorted(hit.reads)), ms=0.0,
                           error=None, ops=0, cached=True), ""

    def _fingerprint(self, proxy: ParamProxy) -> tuple:
        return tuple(sorted(proxy.values.items()))

    def _wrap_custom(self, spec: CheckSpec) -> CheckSpec:
        """Дать проектной проверке доступ к деталям, если она его просит.

        Решает сама проверка — своей сигнатурой. `def проверка(P)` получает
        только параметры, `def проверка(P, parts)` — ещё и построитель деталей:

            parts("шибер")              тело в основном положении
            parts("шибер", u=6.0)       то же на ходе 6 мм

        Так старые проверки на одном аргументе продолжают работать, а новым не
        приходится ни импортировать проект, ни строить деталь руками мимо
        кеша прогона.
        """
        import inspect

        try:
            takes = len(inspect.signature(spec.fn).parameters)
        except (TypeError, ValueError):            # pragma: no cover
            takes = 1
        if takes < 2:
            return spec

        original = spec.fn

        def with_parts(proxy: ParamProxy, _fn=original):
            def parts(name: str, **motion):
                return self._solid(name, proxy, **motion)

            return _fn(proxy, parts)

        return CheckSpec(id=spec.id, fn=with_parts, title=spec.title, group=spec.group)

    def _part_fn(self, name: str, proxy: ParamProxy):
        """Функция хода для проверок развёртки: build(u) → тело в положении u."""

        def build(u: float) -> Solid:
            return self._solid(name, proxy, u=u)

        return build

    def _solid(self, name: str, proxy: ParamProxy, **kwargs) -> Solid:
        """Собрать деталь в рамках текущего прокси (с кешем в пределах прогона).

        СТРАЖ ЗДЕСЬ ОБЯЗАТЕЛЕН. Проверки собирают детали этим путём, а не через
        `build()`, и долгое время страж на нём не звался: деталь с запаянной
        полостью, которую `build` отвергал, в `check` проходила как есть, и
        снимок объявлял её готовой к печати. `expect=` у `@part` на пути
        проверок не значил ничего.

        Отказ стража кешируется наравне с телом: развёртка хода зовёт деталь
        десятки раз, и пересобирать заведомо сломанную незачем.
        """
        from datum.geom.guard import checkpoint

        if name not in self.parts:
            from datum.build.hints import names_hint

            tip = names_hint([name], self.parts)
            raise ProjectError(f"проверка ссылается на несуществующую деталь {name!r}; "
                               f"есть: {sorted(self.parts)}"
                               + (f". Подсказка: {tip}" if tip else ""))
        key = (name, self._fingerprint(proxy), tuple(sorted(kwargs.items())))
        hit = self._cache.get(key)
        if hit is not None:
            outcome, reads = hit
            proxy.mark_read(reads)          # зависимости не теряются на кеше
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        if not kwargs and proxy.values == self.params.resolve():
            outcome, reads = self._solid_built(name)
            proxy.mark_read(reads)
            if name in self._centres:
                self._run_centres[(name, self._fingerprint(proxy))] = self._centres[name]
            self._cache[key] = (outcome, reads)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        from datum.build.worker import stage

        pdef = self.parts[name]
        before = proxy.reads
        outcome: Solid | Exception
        # Контекст проекта — как у `build()`: без него `imported("…")` в
        # скрипте детали не знал, где лежит imported/, и деталь по чужой модели
        # собиралась в окне, но падала в каждой проверке.
        motion = ", ".join(f"{k}={v:g}" if isinstance(v, (int, float)) else f"{k}={v}"
                           for k, v in sorted(kwargs.items()))
        with use_params(proxy), self._context(), \
                stage(f"сборка детали {name}" + (f" ({motion})" if motion else "")):
            solid = pdef.fn(**kwargs)
        if isinstance(solid, Solid):
            # Проверки видят деталь там же, где её показывает окно.
            from datum.doc import placement as _placement

            # Центр — для зон (`zones`); только у детали без хода: в положении
            # u центр другой, и зона повернулась бы не вокруг того.
            if not kwargs:
                self._run_centres[(name, self._fingerprint(proxy))] = \
                    _placement.centre_of(solid)
            solid = _placement.apply(solid, self.placements.get(name))
        if not isinstance(solid, Solid):
            raise ProjectError(f"деталь {name!r} вернула {type(solid).__name__}, а не Solid")
        solid = solid.renamed(name) if not solid.name else solid
        try:
            checkpoint(solid, pdef.expect, mode=self.guard_mode)
            outcome = solid
        except Exception as e:              # GuardError: деталь негодна
            where = f" (ход {kwargs})" if kwargs else ""
            outcome = ProjectError(f"деталь {name!r}{where} не прошла стража: {e}")
        reads = proxy.reads - before
        self._cache[key] = (outcome, reads)
        if len(self._cache) > 256:
            self._cache.pop(next(iter(self._cache)))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def _solid_built(self, name: str) -> tuple[Solid | Exception, frozenset[str]]:
        """Деталь в основном положении при параметрах проекта — через `build()`.

        Тот же путь и тот же дисковый кеш, что у сборки: прежде проверки звали
        скрипт детали сами, и `export` с проверками собирал скульптуру в 330
        тыс. треугольников дважды, а `check_run` после `part_build` — заново,
        упираясь в предел процессорного времени. Отсюда же — что проверки
        меряют РОВНО то тело, что показано в окне и записано в кеш.

        Подставленные значения (развёртка неизвестных) и ход (`u=…`) сюда не
        попадают: их в кеше сборки нет, и они собираются скриптом, как прежде.
        Сбой скрипта не кешируется и уходит в проверку как есть; отказ стража —
        как `ProjectError`, тем же текстом, что на прямом пути.
        """
        from datum.geom.guard import GuardError

        res = self.build([name])[name]
        reads = frozenset(res.depends_on)
        if res.ok:
            solid = res.solid
            return (solid.renamed(name) if not solid.name else solid), reads
        if isinstance(res.error, GuardError):
            return ProjectError(f"деталь {name!r} не прошла стража: {res.error}"), reads
        if res.error is not None:
            raise res.error
        raise ProjectError(f"деталь {name!r} не собралась")       # pragma: no cover

    # ---------------------------------------------------------------- проверки
    def _spec_from_decl(self, decl: dict) -> CheckSpec:  # noqa: C901 — разбор объявления
        if not isinstance(decl, dict) or "use" not in decl:
            raise ProjectError(f"описание проверки должно содержать поле use: {decl!r}")
        use = decl["use"]
        if use not in LIBRARY:
            from datum.build.hints import CHECK_ALIASES, names_hint

            tip = names_hint([str(use)], LIBRARY, aliases=CHECK_ALIASES)
            raise ProjectError(f"неизвестная проверка {use!r}"
                               + (f" (подсказка: {tip})" if tip else "")
                               + f"; каталог: {sorted(LIBRARY)}")
        entry = LIBRARY[use]
        check_id = str(decl.get("id") or use)
        # Важность из описания — только если задана. Раньше умолчание
        # «blocking» перебивало собственную важность проверки: `overhang`
        # (в коде warn) и устаревшая `naive_touch` (info) становились блокирующими.
        severity = decl.get("severity")
        if severity is not None and severity not in ("blocking", "warn", "info"):
            raise ProjectError(f"проверка {check_id!r}: severity {severity!r} — "
                               f"допустимо blocking, warn или info")
        decl = self._with_print_up(decl, entry, check_id)
        decl = self._with_printer(decl, entry, check_id)
        decl = self._with_material(decl, entry)
        if entry.group == "sim":
            # Симуляция — по имени механизма и сценария; механизм обязан быть
            # объявлен (дёшево, при открытии), сценарий проверит прогон.
            sim_args = decl.get("args") or {}
            for key in ("mechanism", "scenario"):
                if not isinstance(sim_args.get(key), str) or not sim_args.get(key):
                    raise ProjectError(f"проверка {check_id!r}: нужен аргумент {key} — имя "
                                       + ("механизма из @mechanism" if key == "mechanism"
                                          else "сценария механизма"))
            if sim_args["mechanism"] not in self.mechanisms:
                raise ProjectError(f"проверка {check_id!r}: нет механизма "
                                   f"{sim_args['mechanism']!r}; есть: "
                                   f"{sorted(self.mechanisms) or 'никаких'}")

        def fn(proxy: ParamProxy, _decl=decl, _entry=entry, _id=check_id, _sev=severity):
            args = resolve_args(_decl.get("args", {}), _entry, proxy)
            # nan во входе проверки (`.nan` в checks.yaml или в параметре)
            # давал зелёную проверку: «упор 89.8 мм³ ≥ nan — OK». Отказ — до
            # расчёта. Бесконечность законна: `max_force: .inf` — «без предела».
            from datum.finite import non_finite

            bad = non_finite(args, "аргументы", allow_inf=True)
            if bad is not None:
                from datum.checks.runner import CheckRefused

                raise CheckRefused(f"{bad[0].removeprefix('аргументы.')} = {bad[1]} — "
                                   f"не число; проверить с ним нельзя")
            # У расчётов-формул (без детали: станки, листы, кулачки) имя в
            # сообщении — из id объявления («каретка.связи» → «каретка»), а не
            # безликое «механизм», «ось» или «флексура»: при двух листах в
            # проекте строка «флексура: ПРЕВЫШЕН» не говорила, какой
            # (найдено обзором — у агента было `крышка.юбка.деформация`).
            if _entry.group == "sim":
                mname, sname = args.pop("mechanism"), args.pop("scenario")
                args["sim"] = self.simulate(mname, sname, proxy,
                                            geometry=_entry.name == "sim_clearance")
                args.setdefault("name", f"{mname} «{sname}»")
                args.setdefault("id", _id)
            formula = not _entry.solid_args and not _entry.fn_args
            if (_entry.group == "machine" or formula) and "name" not in args \
                    and "name" in _accepts(_entry):
                args["name"] = _id.rsplit(".", 1)[0] if "." in _id else _id

            # Ссылки «@имя» превращаем в собранные тела (или в функцию от хода,
            # если проверка ждёт именно её).
            #
            # Разбор идёт ВГЛУБЬ, по спискам и отображениям: у развёртки хода
            # аргумент `others` — это список деталей, и без обхода в проверку
            # уезжал PartRef, падая там невнятным «unsupported operand for ^».
            def materialise(value):
                if isinstance(value, PartRef):
                    return self._solid(value.name, proxy)
                if isinstance(value, PartFnRef):
                    # Проверки хода зовут build(u) позиционно, а _solid принимает
                    # ход только по имени — поэтому замыкание, а не partial.
                    return self._part_fn(value.name, proxy)
                if isinstance(value, list):
                    return [materialise(v) for v in value]
                if isinstance(value, tuple):
                    return tuple(materialise(v) for v in value)
                if isinstance(value, dict):
                    return {k: materialise(v) for k, v in value.items()}
                return value

            wanted_zones = args.pop("exclude_zones", None)
            args = {k: materialise(v) for k, v in args.items()}
            if wanted_zones is not None:
                args["exclude_zones"] = self._zone_solids(_id, _decl, _entry, wanted_zones,
                                                          proxy)

            results = _entry.fn(**args)
            out = list(results) if isinstance(results, (list, tuple)) else [results]

            fixed: list[CheckResult] = []
            single = len(out) == 1
            for r in out:
                # Идентификатор из описания — ведущий. У одиночного результата он
                # и остаётся; у набора (например по направлениям) добавляется
                # хвост, иначе результаты слипнутся в один.
                if r.id == _id or r.id.startswith(_id + "."):
                    rid = r.id
                elif single:
                    rid = _id
                else:
                    tail = r.id.rsplit(".", 1)[-1]
                    rid = f"{_id}.{tail}"
                status = r.status
                if _sev == "blocking" and r.severity == "warn" and r.status == "warn":
                    # Проект строже каталога: мягкий отказ (нависания сверх
                    # доли) объявлен блокирующим — значит, это отказ. Прочие
                    # «warn» (порог внутри вилки МКЭ) — не отказ, а
                    # неопределённость, и важностью не превращаются.
                    status = "fail"
                fixed.append(replace(r, id=rid, severity=_sev or r.severity, status=status))
            return fixed

        return CheckSpec(id=check_id, fn=fn, title=str(decl.get("title", "")),
                         group=entry.group)

    def _with_print_up(self, decl: dict, entry, check_id: str) -> dict:
        """Подставить в проверку печатности ориентацию, объявленную у детали.

        Ориентация — свойство детали, а не проверки: проверок печатности у
        одной детали бывает пять (прилипание, поддержки, мосты, нависания,
        габарит), и пять копий `up` разъехались бы. Поэтому задаётся она в
        `@part(print_up=...)`, а здесь только переносится.

        `up` в самой проверке, отличный от объявленного у детали, — ошибка до
        всякого расчёта, а не тихое предпочтение одного из двух: какое из них
        верно, знает только человек.
        """
        import inspect

        try:
            takes_up = "up" in inspect.signature(entry.fn).parameters
        except (TypeError, ValueError):            # pragma: no cover
            takes_up = False
        args = decl.get("args") or {}
        ref = args.get("part") if isinstance(args, dict) else None
        if not (takes_up and "part" in entry.solid_args
                and isinstance(ref, str) and ref.startswith("@")):
            return decl
        pdef = self.parts.get(ref[1:])
        declared = pdef.print_up if pdef is not None else ""
        if not declared:
            return decl
        if "up" in args:
            given = args["up"]
            # Проверки сами понимают «+x» и пробелы по краям — сравнение тоже.
            if not (isinstance(given, str) and given.strip().upper() == declared):
                raise ProjectError(
                    f"проверка {check_id!r}: up={args['up']!r}, а у детали {ref[1:]!r} "
                    f"объявлено print_up={declared!r}. Ориентация задаётся у детали; "
                    f"уберите up из проверки или исправьте деталь")
            return decl
        return {**decl, "args": {**args, "up": declared}}

    def _with_printer(self, decl: dict, entry, check_id: str) -> dict:
        """Подставить стол и слой из профиля принтера в `datum.toml`.

        Та же болезнь, что была с ориентацией: стол 256×256×256 переписывался
        руками в каждом `checks.yaml`, а профиль принтера в манифесте не читал
        никто. Сменили принтер в манифесте — проверки об этом не узнали бы.
        Заданное в проверке значение, отличное от профиля, — ошибка: какое из
        двух верно, знает человек.
        """
        import inspect

        try:
            takes = inspect.signature(entry.fn).parameters
        except (TypeError, ValueError):            # pragma: no cover
            return decl
        printer = self.manifest.get("printer", {}) or {}
        supplied = {"bed": list(self.bed)}
        if "layer" in printer:
            supplied["layer"] = float(printer["layer"])
        args = dict(decl.get("args") or {})
        changed = False
        for key, value in supplied.items():
            if key not in takes:
                continue
            if key in args:
                given = args[key]
                same = (list(map(float, given)) == [float(v) for v in value]
                        if isinstance(value, list) and isinstance(given, (list, tuple))
                        else isinstance(given, (int, float)) and float(given) == value)
                if not same:
                    raise ProjectError(
                        f"проверка {check_id!r}: {key}={given!r}, а в профиле принтера "
                        f"(datum.toml) {value!r}. Принтер задаётся в манифесте; уберите "
                        f"{key} из проверки или исправьте профиль")
                continue
            args[key] = value
            changed = True
        return {**decl, "args": args} if changed else decl

    def zones(self, name: str, proxy: ParamProxy | None = None) -> dict[str, Solid]:
        """Зоны детали (`@part(zones=…)`) — телами, там же, где стоит деталь.

        Поворот — вокруг центра ДЕТАЛИ, собранной при тех же значениях: так
        зона остаётся на язычке при любом положении на столе.
        """
        from datum.doc import placement as _placement

        pdef = self.parts[name]
        if pdef.zones is None:
            return {}
        proxy = proxy or self.params.proxy()
        with use_params(proxy), self._context():
            raw = pdef.zones()
        if not isinstance(raw, dict) or not all(
                isinstance(k, str) and isinstance(v, Solid) for k, v in raw.items()):
            raise ProjectError(f"деталь {name!r}: zones обязана вернуть {{имя: тело}}, "
                               f"получено {type(raw).__name__}")
        where = self.placements.get(name)
        if where is None or where.identity:
            return {k: v.renamed(k) for k, v in raw.items()}
        key = (name, self._fingerprint(proxy))
        if key not in self._run_centres:
            self._solid(name, proxy)            # соберёт и запомнит центр
        centre = self._run_centres[key]
        return {k: _placement.apply(v, where, centre=centre).renamed(k) for k, v in raw.items()}

    # ------------------------------------------------- печать и симуляция
    def print_settings(self, name: str, proxy: ParamProxy | None = None):
        """Настройки печати детали: профиль слайсера ← [print] ← @part(print=…).

        Имя параметра в настройке читается через `proxy` — зависимость видна,
        развёртка гоняет и её."""
        from datum.fab.settings import SettingsError, from_profiles, resolve

        if name not in self.parts:
            raise ProjectError(f"нет детали {name!r}")
        base = self.__dict__.get("_print_base")
        if base is None:
            base = from_profiles(self.manifest.get("printer", {}))
            self.__dict__["_print_base"] = base
        proxy = proxy or self.params.proxy()
        layer = float((self.manifest.get("printer", {}) or {}).get("layer", 0.2))
        try:
            return resolve(base[0], base[1], layer=layer, project=self.manifest.get("print", {}),
                           part=self.parts[name].print_cfg or {}, part_name=name,
                           read_param=lambda n: proxy[n])
        except SettingsError as e:
            raise ProjectError(str(e)) from e

    def mechanism(self, name: str, proxy: ParamProxy | None = None):
        """Механизм `@mechanism(name)` — собранный с параметрами `proxy`."""
        from datum.sim.mechanism import Mechanism, MechanismError

        if name not in self.mechanisms:
            from datum.build.hints import names_hint

            tip = names_hint([name], self.mechanisms) if self.mechanisms else ""
            raise ProjectError(f"нет механизма {name!r}; есть: {sorted(self.mechanisms) or 'никаких'}"
                               + (f". Подсказка: {tip}" if tip else "")
                               + ("" if self.mechanisms else
                                  " — объявите @mechanism в parts/*.py (references/simulation.md)"))
        proxy = proxy or self.params.proxy()
        try:
            with use_params(proxy), self._context():
                mech = self.mechanisms[name].fn()
        except MechanismError as e:
            raise ProjectError(f"механизм {name!r}: {e}") from e
        if not isinstance(mech, Mechanism):
            raise ProjectError(f"механизм {name!r}: функция обязана вернуть Mechanism, "
                               f"получено {type(mech).__name__}")
        try:
            mech.validate()
        except MechanismError as e:
            raise ProjectError(f"механизм {name!r}: {e}") from e
        mech._name = name                                   # для сообщений проверок
        for lk in mech.links.values():
            for piece in lk.pieces:
                if piece.part is not None and piece.part not in self.parts:
                    raise ProjectError(f"механизм {name!r}, звено {lk.name!r}: нет детали "
                                       f"{piece.part!r}; есть: {sorted(self.parts)}")
        return mech

    def compiled_mechanism(self, name: str, proxy: ParamProxy | None = None):
        """Звенья механизма с массами по настройкам печати (см. `sim.compile`)."""
        from datum.doc.params import ParamProxy as _Proxy
        from datum.sim.compile import compile_mechanism

        proxy = proxy or self.params.proxy()
        key = ("механизм", name, self._fingerprint(proxy))
        if key not in self._sim:
            # Свежий прокси с теми же значениями: его чтения — ровно то, от чего
            # зависит механизм. Их получает каждая проверка, взявшая его из
            # кеша, иначе вторая проверка того же механизма теряла бы зависимости.
            fresh = _Proxy(proxy.values)
            mech = self.mechanism(name, fresh)

            def settings(part_name: str):
                return self.print_settings(part_name, fresh), self.parts[part_name].print_up

            comp = compile_mechanism(mech, part_settings=settings,
                                     cache_dir=self.root / ".datum" / "mass")
            self._sim[key] = (comp, fresh.reads)
        comp, reads = self._sim[key]
        proxy.mark_read(reads)
        return comp

    def simulate(self, name: str, scenario: str, proxy: ParamProxy | None = None, *,
                 geometry: bool = True):
        """Прогон сценария механизма — один раз на (механизм, сценарий, значения):
        несколько проверок одного сценария берут общий результат."""
        from datum.sim.mechanism import MechanismError
        from datum.sim.run import run_scenario

        proxy = proxy or self.params.proxy()
        comp = self.compiled_mechanism(name, proxy)       # чтения пишутся в proxy
        key = (name, scenario, geometry, self._fingerprint(proxy))
        if key not in self._sim:
            try:
                self._sim[key] = (comp, run_scenario(comp, scenario, geometry=geometry))
            except MechanismError as e:
                raise ProjectError(f"механизм {name!r}: {e}") from e
        return self._sim[key]

    def _zone_solids(self, check_id: str, decl: dict, entry, wanted, proxy) -> list:
        """`exclude_zones: [имя, …]` проверки → тела зон её детали."""
        import inspect

        if "exclude_zones" not in inspect.signature(entry.fn).parameters:
            raise ProjectError(f"проверка {check_id!r}: {entry.name} зон не исключает")
        if isinstance(wanted, str):
            wanted = [wanted]
        if not (isinstance(wanted, list) and all(isinstance(w, str) for w in wanted)):
            raise ProjectError(f"проверка {check_id!r}: exclude_zones — список имён зон, "
                               f"получено {wanted!r}")
        ref = (decl.get("args") or {}).get("part")
        if not (isinstance(ref, str) and ref.startswith("@")):
            raise ProjectError(f"проверка {check_id!r}: зоны ищутся у детали part=«@имя»")
        name = ref[1:]
        if name not in self.parts:
            raise ProjectError(f"проверка {check_id!r}: нет детали {name!r}")
        zones = self.zones(name, proxy)
        missing = [w for w in wanted if w not in zones]
        if missing:
            raise ProjectError(f"проверка {check_id!r}: у детали {name!r} нет зон {missing}; "
                               f"есть: {sorted(zones) or 'никаких (@part(zones=…))'}")
        return [zones[w] for w in wanted]

    #: Аргумент проверки → параметр каталога материала, которым он заполняется.
    MATERIAL_ARGS = {"modulus": "MAT_E", "density": "MAT_DENSITY",
                     "strength_z": "MAT_TENSILE_Z", "strength_xy": "MAT_TENSILE_XY"}

    def _with_material(self, decl: dict, entry) -> dict:
        """Модуль и плотность — из материала проекта, если в проверке их нет.

        Ссылкой на параметр, а не числом: так зависимость видна в отчёте, и
        развёртка гоняет угаданный модуль по его диапазону.
        """
        import inspect

        try:
            takes = inspect.signature(entry.fn).parameters
        except (TypeError, ValueError):            # pragma: no cover
            return decl
        args = dict(decl.get("args") or {})
        added = {arg: f"${{params.{name}}}" for arg, name in self.MATERIAL_ARGS.items()
                 if arg in takes and arg not in args and name in self.params}
        return {**decl, "args": {**args, **added}} if added else decl

    def derived_decls(self) -> list[dict]:
        """Проверки, выведенные из порядка сборки.

        Объявленная руками проверка с тем же идентификатором ПЕРЕБИВАЕТ
        выведенную: порядок задаёт разумное умолчание, а частный случай
        (особые препятствия, снятые пружины) человек опишет точнее. Сколько
        таких перебито — видно в сводке, чтобы подмена не прошла незаметно.
        """
        declared = {str(d.get("id") or d.get("use")) for d in self.check_decls}
        derived = self.assembly.check_decls() + self.baseline_decls()
        return [d for d in derived if d["id"] not in declared]

    #: Базовые проверки печатности каждой детали: (проверка, хвост id).
    BASELINE = (("min_wall", "стенка"), ("no_sealed_void", "без_пузырей"),
                ("bbox_fits", "на_столе"), ("overhang", "нависания"))

    def baseline_decls(self) -> list[dict]:
        """Проверки печатности, которые есть у КАЖДОЙ детали, если их не
        объявили руками.

        Прежде `datum init` давал пустой checks.yaml, и проект, где агент их не
        добавил, выгружался «готовым к печати»: стенка 0.8 мм (у паза 0.2) ушла
        с отметкой ГОТОВ (найдено обзором — агент проектировал коробку с нуля).
        Объявленная руками проверка того же вида для той же детали перебивает
        базовую — со своими порогами и зонами. Выключить для всего проекта —
        `[checks] baseline = false` в datum.toml.
        """
        cfg = self.manifest.get("checks", {}) or {}
        if cfg.get("baseline", True) is False:
            return []
        own = {(str(d.get("use")), str((d.get("args") or {}).get("part", "")))
               for d in self.check_decls if isinstance(d, dict)}
        # Базовая стенка — «напечатается ли вообще»: одна линия сопла, тоньше
        # слайсер стенку не кладёт. Правило мастерской (1.6 мм, прочность)
        # объявляют руками. Замер по примерам: с 1.6 краснели законно тонкие
        # флексура дозатора и губка Gridfinity, с двумя линиями (0.8) — борт
        # плиты Gridfinity, сходящий по спецификации на острие; с одной —
        # примеры чисты, а паз, оставивший 0.2 мм, ловится.
        nozzle = float((self.manifest.get("printer", {}) or {}).get("nozzle", 0.4))
        extra = {"min_wall": {"wall": round(nozzle, 3)}}
        out = []
        for name, pdef in self.parts.items():
            # Составная деталь (тел не одно — показная сборка, группа, печатаемая
            # вместе): ни одно тело не должно висеть в воздухе. «Камень в
            # воздухе» на сборке Gear Cube нашёл человек, а не проверка.
            # Базовая — ПРЕДУПРЕЖДЕНИЕ, не отказ: в макете между звеньями
            # законно пусто там, где стоит покупное, которого в модели нет
            # (подшипники руки-v3 — 7 «висящих» тел из 12). Блокирующей её
            # объявляют руками, со своим `gap`. Детали из одного тела проверка
            # не нужна — и её нет.
            if pdef.expect.bodies != 1 and ("no_floating_body", f"@{name}") not in own:
                out.append({"id": f"{name}.без_висящего", "use": "no_floating_body",
                            "args": {"part": f"@{name}"}, "baseline": True,
                            "severity": "warn"})
            if not pdef.printed:
                continue                 # покупное и показное: «напечатается ли» — не о нём
            for use, tail in self.BASELINE:
                if (use, f"@{name}") in own:
                    continue
                if use == "no_sealed_void" and pdef.expect.allow_sealed_voids:
                    # Полость объявлена намеренной у самой детали (поплавок) —
                    # страж сборки её пропускает, и базовая проверка обязана
                    # тоже: прежде она краснела там, где деталь сказала «можно».
                    continue
                out.append({"id": f"{name}.{tail}", "use": use,
                            "args": {"part": f"@{name}", **extra.get(use, {})},
                            "baseline": True})
        return out

    def specs(self) -> list[CheckSpec]:
        decls = list(self.check_decls) + self.derived_decls()
        return [self._spec_from_decl(d) for d in decls] + list(self.custom_specs)

    def unread_params(self, run: CheckRun) -> list[str] | None:
        """Параметры, которых не читает НИ ОДНА деталь и НИ ОДНА проверка.

        Почти всегда это ошибка: размер объявлен, но не использован — правят его,
        а деталь не меняется (в разборе sand3d так у 31 % программ). Чтения
        известны точно: сборка пишет их у каждой детали, проверки — у каждого
        результата, вместе с производными. Параметры каталога материалов
        (`MAT_*`) проект получает сам, и не читать их — норма.

        None — судить нельзя: деталь не собралась, и что она читает, неизвестно.
        """
        built = self.build()
        if not all(r.ok for r in built.values()):
            return None
        read: set[str] = set()
        for res in built.values():
            read |= set(res.depends_on)
        for result in run.results:
            read |= set(result.depends_on)
        for name in list(read):
            read |= set(self.params.depends_on(name))
        return sorted(p.name for p in self.params
                      if p.name not in read and not p.name.startswith("MAT_"))

    def run_checks(self, overrides: dict[str, float] | None = None, *,
                   only: list[str] | None = None, parts: list[str] | None = None,
                   group: str = "", fresh: bool = False) -> CheckRun:
        """Прогнать проверки — все или выбранные (`select_specs`).

        Выбор сужает ПРОГОН, а не вердикт проекта: «ИТОГ» выбранного — не
        «ИТОГ» проекта, и операция обязана сказать это словами.

        Результат проверки, входы которой не менялись, берётся из кеша
        (`doc/checkcache.py` — там же, что входит в ключ). `fresh=True` —
        пересчитать всё заново. Сколько взято из кеша — в `check_cache_use`.
        """
        self._cache.clear()
        specs = self.specs()
        if only or parts or group:
            specs = self.select_specs(specs, only=only, parts=parts, group=group)
        cache = None if fresh else self._check_cache()
        self.check_cache_use = (0, 0)
        if cache is None:
            run = self._publish_evidence(run_checks(specs, self.params, overrides))
        else:
            from datum.checks.runner import _run_one

            values = self.params.resolve(overrides)
            results: list[CheckResult] = []
            try:
                for spec in specs:
                    kept = cache.lookup(spec.id, values)
                    if kept is None:
                        found, reads = _run_one(spec, self.params, overrides)
                        found = list(self._publish_evidence(CheckRun(tuple(found))).results)
                        # Ключ — по ЗНАЧЕНИЯМ прочитанного; имя, которого нет
                        # среди разрешённых значений, ключом не выразить.
                        if all(name in values for name in reads):
                            cache.store(spec.id, {n: values[n] for n in reads}, found)
                        kept = found
                    results.extend(kept)
                self.check_cache_use = (cache.hits, cache.hits + cache.misses)
                cache.trim()
            finally:
                cache.close()
            run = CheckRun(tuple(results))
        if only or parts:
            run = CheckRun(tuple(r for r in run.results
                                 if _result_selected(r, only or [], parts or [])))
        return run

    def _check_cache(self):
        """Кеш результатов проверок на этот прогон — или None, когда выключен
        (`[checks] cache = false` в datum.toml) либо его не открыть."""
        if not (self.manifest.get("checks") or {}).get("cache", True):
            return None
        from datum.doc import checkcache

        try:
            from datum.fab.settings import from_profiles

            profile = from_profiles(self.manifest.get("printer", {}))
        except Exception as e:                    # профилей нет — это тоже состояние
            profile = f"нет профиля: {type(e).__name__}"
        try:
            context = checkcache.context_digest(
                checkcache.datum_digest(), checkcache.files_digest(self.root), profile,
                self.guard_mode.value)
            return checkcache.CheckCache(self.root, context=context)
        except Exception:                         # кеш — удобство, а не условие прогона
            return None

    def select_specs(self, specs: list[CheckSpec], *, only: list[str] | None = None,
                     parts: list[str] | None = None, group: str = "") -> list[CheckSpec]:
        """Проверки, которые стоит запускать под фильтр.

        `only` — id или шаблоны (`крышка.*`); id результата бывает длиннее id
        объявления (`крышка.зацеп.+Z` у `крышка.зацеп`), поэтому объявление
        берётся и по своему id, и как начало названного. `parts` — проверки,
        в аргументах которых названа деталь; проектные (`custom.py`) своих
        деталей заранее не объявляют — они запускаются, а их результаты
        отбираются по `inputs` уже после. Неизвестное имя — отказ: тихо пустой
        прогон выглядел бы как «проверять нечего».
        """
        import fnmatch

        only, parts = list(only or []), list(parts or [])
        unknown = [n for n in parts if n not in self.parts]
        if unknown:
            raise ProjectError(f"нет таких деталей: {unknown}; есть: {sorted(self.parts)}")
        groups = sorted({s.group for s in specs if s.group})
        if group and group not in groups:
            raise ProjectError(f"нет группы проверок {group!r}; есть: {groups}")
        declared = self._declared_parts()
        chosen = []
        for spec in specs:
            if group and spec.group != group:
                continue
            if only and not any(fnmatch.fnmatchcase(spec.id, pat) or pat == spec.id
                                or pat.startswith(spec.id + ".") for pat in only):
                continue
            named = declared.get(spec.id)
            if parts and named is not None and not (set(named) & set(parts)):
                continue
            chosen.append(spec)
        if only and not chosen:
            ids = sorted(s.id for s in specs)
            raise ProjectError(f"ни одна проверка не подходит под {only}; есть: "
                               + ", ".join(ids[:40]) + (" …" if len(ids) > 40 else ""))
        return chosen

    def _declared_parts(self) -> dict[str, list[str]]:
        """Детали, названные в аргументах объявленных проверок: id → имена.
        Проектных проверок и проверок без ссылок на детали здесь нет — их
        детали известны только по результату."""
        def names(value) -> list[str]:
            if isinstance(value, str):
                # «@крышка» — ссылка на деталь; голое имя тоже принимают
                # некоторые проверки (подпись детали в сообщении).
                name = value[1:] if value.startswith("@") else value
                return [name] if name in self.parts else []
            if isinstance(value, (list, tuple)):
                return [n for v in value for n in names(v)]
            if isinstance(value, dict):
                return [n for v in value.values() for n in names(v)]
            return []

        out: dict[str, list[str]] = {}
        for decl in list(self.check_decls) + self.derived_decls():
            if isinstance(decl, dict) and "use" in decl:
                found = names(decl.get("args") or {})
                # Деталей в аргументах нет (формула, симуляция по механизму) —
                # заранее не судим: запустим и отберём по `inputs` результата.
                if found:
                    out[str(decl.get("id") or decl["use"])] = found
        return out

    def _publish_evidence(self, run: CheckRun) -> CheckRun:
        """Положить доказательства в кеш и проставить им адреса.

        Проверки возвращают доказательство БАЙТАМИ и о кеше не знают — это
        верно: проверка считает, а не хранит. Но байты нельзя ни показать в
        окне, ни передать агенту, поэтому здесь они кладутся в тот же
        контент-адресуемый кеш, что и геометрия, и получают адрес `/obj/...`.

        Отпечаток — содержимое картинки. Одна и та же беда даёт тот же адрес, и
        повторный прогон не плодит файлов.
        """
        import dataclasses

        if not any(r.evidence for r in run.results):
            return run

        cache = self.cache
        results = []
        for result in run.results:
            if not result.evidence:
                results.append(result)
                continue
            published = []
            for artifact in result.evidence:
                data = getattr(artifact, "data", None)
                if not isinstance(data, (bytes, bytearray)):
                    published.append(artifact)
                    continue
                ext = artifact.kind if artifact.kind in ("png", "glb") else "json"
                digest = cache.put_blob(bytes(data), ext, kind="evidence",
                                        meta={"check": result.id,
                                              "caption": artifact.caption})
                blob = cache.blob_path(digest, ext)
                relative = (str(blob.relative_to(self.root))
                            if blob is not None and blob.is_relative_to(self.root)
                            else (str(blob) if blob else ""))
                published.append(dataclasses.replace(
                    artifact, data=None, digest=digest,
                    url=f"/obj/{digest}.{ext}", path=relative))
            results.append(dataclasses.replace(result, evidence=tuple(published)))
        return dataclasses.replace(run, results=tuple(results))

    def sweep(self, params: list[str] | None = None) -> Sensitivity:
        self._cache.clear()
        return sweep_assumed(self.specs(), self.params, params=params)

    # ---------------------------------------------------------------- сведения
    def summary(self) -> dict:
        derived = self.derived_decls()
        return {
            "name": self.name, "root": str(self.root),
            "parts": sorted(self.parts),
            # Покупное и показное (`printed=False`): в проверках есть, в печать нет.
            "not_printed": sorted(n for n, d in self.parts.items() if not d.printed),
            "checks": len(self.check_decls) + len(derived) + len(self.custom_specs),
            # Базовые печатности — отдельно: «выведено из порядка сборки» ниже
            # прежнее, иначе одно число смешало бы два источника.
            "baseline_checks": sum(1 for d in derived if d.get("baseline")),
            "assembly": {
                "order": list(self.assembly.order),
                "derived_checks": sum(1 for d in derived if not d.get("baseline")),
                "overridden": len(self.assembly.check_decls())
                              - sum(1 for d in derived if not d.get("baseline")),
                # Деталь вне порядка не проверяется на собираемость. Молчать об
                # этом нельзя: отчёт выглядел бы так же зелено.
                "unlisted": self.assembly.unlisted(self.parts),
                "explode_gap": self.assembly.explode_gap,
                # Способ и направление нужны окну: разносить деталь осмысленно
                # ВДОЛЬ ТОГО, КАК ОНА ВХОДИТ, а не куда попало.
                "steps": {name: {"method": st.method,
                                 "insert": list(st.insert),
                                 "distance": st.distance}
                          for name, st in self.assembly.steps.items()},
                # Шарниры — окну, чтобы проиграть ход: ось вектором (имя
                # направления окну не разбирать), пределы и id проверки хода.
                "joints": {name: {"kind": j.kind, "axis": _axis_vector(j.axis),
                                  "origin": list(j.origin), "range": list(j.limits),
                                  "check": j.check_id}
                           for name, j in self.assembly.joints.items()},
            },
            "params": self.params.summary(),
            "printer": self.manifest.get("printer", {}),
            "guard": self.guard_mode.value,
        }


def _axis_vector(axis) -> list[float]:
    """Ось шарнира единичным вектором — так же, как её понимает проверка."""
    from datum.checks.motion import direction

    return [round(float(v), 12) + 0.0 for v in direction(axis)]   # без «-0.0» в JSON


def check(id: str, *, title: str = "", group: str = "custom"):
    """Пометить функцию как проектную проверку (для `checks/custom.py`)."""

    def wrap(fn):
        fn._datum_check_id = id
        fn._datum_check_title = title
        fn._datum_check_group = group
        return fn

    return wrap
