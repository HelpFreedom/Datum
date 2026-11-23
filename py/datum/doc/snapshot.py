"""Снимок версии проекта и сравнение снимков.

Претензия из ТЗ: «Сравнение версий. „Что изменилось между v12 и v13“ я вёл
руками в NOTES.md. Инструмент, который сравнивает две сборки по объёму,
габаритам и таблице проверок, делал бы регрессии очевидными.»

Снимок хранит не геометрию, а её отпечаток и метрики: файл получается мелким,
ложится в git и читается глазами. Меши остаются в кеше, а снимок на них
ссылается по дайджесту — если содержимое не изменилось, дайджест тот же, и это
само по себе ответ на вопрос «что поменялось».
"""

from __future__ import annotations

import json
import math
import platform
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from datum.doc.materials import CATALOG

__all__ = ["PartMetrics", "Snapshot", "SNAPSHOT_DIR", "DENSITY", "GEOMETRY_DIR",
           "keep_geometry", "kept_geometry"]

SNAPSHOT_DIR = "snapshots"

#: Форма деталей снимков — для сравнения картинкой (`doc/geomdiff.py`). Не в
#: `snapshots/`: тот ложится в git и читается глазами, а здесь двоичные сетки.
#: Не в кеше сборок: тот чистится по давности, и картинка старой версии
#: пропадала бы. Имя файла — отпечаток формы (`Solid.digest`), так что
#: одинаковая деталь в десяти снимках лежит один раз.
GEOMETRY_DIR = Path(".datum") / "snapshot-meshes"


def keep_geometry(root: str | Path, solid) -> str:
    """Сохранить форму детали снимка (если её ещё нет). Возвращает отпечаток."""
    import os
    import tempfile

    from datum.build.cache import solid_to_bytes

    digest = solid.digest()
    folder = Path(root) / GEOMETRY_DIR
    target = folder / f"{digest}.mesh"
    if target.is_file():
        return digest
    folder.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(solid_to_bytes(solid))
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return digest


def kept_geometry(root: str | Path, digest: str, *, name: str = ""):
    """Форма детали снимка по отпечатку — или None, если её нет (снимок снят
    до появления картинок, проект распакован без `.datum/`)."""
    from datum.build.cache import solid_from_bytes

    import re

    # Отпечаток приходит из файла снимка, а его правят руками: путь из него
    # строится только по шестнадцатеричному имени.
    if not re.fullmatch(r"[0-9a-f]{16,128}", digest or ""):
        return None
    path = Path(root) / GEOMETRY_DIR / f"{digest}.mesh"
    if not path.is_file():
        return None
    return solid_from_bytes(path.read_bytes(), name=name)

#: Плотность, г/см³ — для оценки массы по объёму, когда у проекта нет
#: `MAT_DENSITY`. Числа — из каталога материалов (одна правда на оба места:
#: прежняя своя таблица разошлась с ним — PLA 1.24 против 1.26); сверх
#: каталога — только то, чего в нём нет.
DENSITY: dict[str, float] = {**{k: m.density for k, m in CATALOG.items()}, "PA": 1.14}


def _part_mass(project, name: str, solid, volume: float, density: float) -> tuple[float, str]:
    """Масса детали для снимка — ТОЙ ЖЕ функцией, что у `part_mass`, симуляции и
    ведомости (`fab.mass.cached_print_mass` по настройкам печати детали).

    Прежде снимок считал объём × плотность, и «масса» в версиях была в
    полтора-два раза больше «массы» в `part_mass` (аудит 2026-11-21, §6.2.7).
    Покупное и показное пластика не стоит. Модель слоёв не посчиталась —
    прежняя оценка, и снимок это помнит (`mass_by`)."""
    pdef = project.parts.get(name)
    if pdef is not None and not pdef.printed:
        return 0.0, "не печатается"
    try:
        from datum.fab.mass import cached_print_mass

        props = cached_print_mass(solid, project.print_settings(name),
                                  up=pdef.print_up if pdef is not None else "",
                                  cache_dir=project.root / ".datum" / "mass")
        if math.isfinite(props.mass_g):
            return float(props.mass_g), "печать"
    except Exception:                                # noqa: BLE001 — снимок важнее массы
        pass
    return volume / 1000.0 * density, "сплошное"


@dataclass(frozen=True, slots=True)
class PartMetrics:
    name: str
    ok: bool
    source_digest: str = ""
    #: Код проекта целиком: файл детали и `lib/` (`project_code_digest`).
    #: Одно тело функции не видит правки помощника или константы модуля.
    code_digest: str = ""
    result_digest: str = ""
    volume_mm3: float = 0.0
    area_mm2: float = 0.0
    bbox_min: tuple[float, float, float] = (0.0, 0.0, 0.0)
    bbox_max: tuple[float, float, float] = (0.0, 0.0, 0.0)
    bodies: int = 0
    sealed_voids: int = 0
    genus: int = 0
    triangles: int = 0
    mass_g: float = 0.0
    #: Чем посчитана масса: «печать» — по настройкам печати, как у `part_mass`,
    #: симуляции и ведомости; «сплошное» — объём × плотность (модель слоёв не
    #: посчиталась); «не печатается» — покупное и показное, масса 0. Пусто —
    #: снимок старше этого поля: там объём × плотность.
    mass_by: str = ""
    depends_on: tuple[str, ...] = ()
    error: str = ""

    @property
    def extents(self) -> tuple[float, float, float]:
        return tuple(self.bbox_max[i] - self.bbox_min[i] for i in range(3))

    def as_dict(self) -> dict:
        d = asdict(self)
        for k in ("bbox_min", "bbox_max"):
            d[k] = [round(v, 4) for v in d[k]]
        for k in ("volume_mm3", "area_mm2", "mass_g"):
            d[k] = round(d[k], 4)
        d["depends_on"] = list(d["depends_on"])
        return d

    @staticmethod
    def from_dict(d: dict) -> "PartMetrics":
        return PartMetrics(
            name=d["name"], ok=d["ok"],
            source_digest=d.get("source_digest", ""),
            code_digest=d.get("code_digest", ""),
            result_digest=d.get("result_digest", ""),
            volume_mm3=d.get("volume_mm3", 0.0), area_mm2=d.get("area_mm2", 0.0),
            bbox_min=tuple(d.get("bbox_min", (0, 0, 0))),
            bbox_max=tuple(d.get("bbox_max", (0, 0, 0))),
            bodies=d.get("bodies", 0), sealed_voids=d.get("sealed_voids", 0),
            genus=d.get("genus", 0), triangles=d.get("triangles", 0),
            mass_g=d.get("mass_g", 0.0),
            mass_by=d.get("mass_by", ""),
            depends_on=tuple(d.get("depends_on", ())), error=d.get("error", ""))


@dataclass(frozen=True, slots=True)
class Snapshot:
    tag: str
    created: str
    project: str
    params: dict[str, float] = field(default_factory=dict)
    params_meta: dict[str, dict] = field(default_factory=dict)
    parts: dict[str, PartMetrics] = field(default_factory=dict)
    checks: dict = field(default_factory=dict)
    manifest: dict = field(default_factory=dict)
    notes: str = ""
    environment: dict = field(default_factory=dict)

    # ---------------------------------------------------------------- свойства
    @property
    def print_ready(self) -> bool:
        """Готов ли к печати: сборка цела, проверки прошли, незамеренного нет."""
        return (all(p.ok for p in self.parts.values())
                and bool(self.checks.get("ok", False))
                and not self.params_meta_blocking())

    def params_meta_blocking(self) -> list[str]:
        return [n for n, m in self.params_meta.items()
                if m.get("provenance") == "assumed" and m.get("blocking")]

    @property
    def total_mass_g(self) -> float:
        return sum(p.mass_g for p in self.parts.values())

    @property
    def mass_by_print(self) -> bool:
        """Все массы снимка — по настройкам печати (а не объём × плотность)."""
        return all(p.mass_by in ("печать", "не печатается")
                   for p in self.parts.values() if p.ok)

    # ---------------------------------------------------------------- создание
    @staticmethod
    def create(project, tag: str, *, notes: str = "",
               overrides: dict[str, float] | None = None) -> "Snapshot":
        from datum.geom.measure import area, bbox

        material = str(project.manifest.get("printer", {}).get("material", "PLA"))
        # Плотность — из каталога материала (параметр MAT_DENSITY, его может
        # перебить проект); нет в каталоге — прежняя таблица.
        if "MAT_DENSITY" in project.params:
            density = float(project.params.resolve()["MAT_DENSITY"])
        else:
            density = DENSITY.get(material.upper(), DENSITY["PLA"])

        built = project.build(overrides=overrides)
        parts: dict[str, PartMetrics] = {}
        for name, res in built.items():
            if not res.ok:
                parts[name] = PartMetrics(name=name, ok=False,
                                          source_digest=_digest_of(project, name),
                                          code_digest=_code_digest_of(project, name),
                                          depends_on=res.depends_on,
                                          error=str(res.error))
                continue
            t = res.topology
            lo, hi = bbox(res.solid)
            keep_geometry(project.root, res.solid)
            # Снимок — история версий: число, которое не число, туда не ложится.
            measured = [t.volume, *lo, *hi]
            if not all(math.isfinite(v) for v in measured):
                parts[name] = PartMetrics(name=name, ok=False,
                                          source_digest=_digest_of(project, name),
                                          code_digest=_code_digest_of(project, name),
                                          depends_on=res.depends_on,
                                          error="объём или габарит детали — не число "
                                                "(геометрия выродилась)")
                continue
            mass_g, mass_by = _part_mass(project, name, res.solid, t.volume, density)
            parts[name] = PartMetrics(
                name=name, ok=True,
                source_digest=_digest_of(project, name),
                code_digest=_code_digest_of(project, name),
                result_digest=res.solid.digest(),
                volume_mm3=t.volume, area_mm2=area(res.solid),
                bbox_min=tuple(round(v, 6) for v in lo),
                bbox_max=tuple(round(v, 6) for v in hi),
                bodies=t.bodies, sealed_voids=t.sealed_voids, genus=t.genus,
                triangles=t.triangles,
                mass_g=mass_g, mass_by=mass_by,
                depends_on=res.depends_on)

        run = project.run_checks(overrides)
        return Snapshot(
            tag=tag,
            created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            project=project.name,
            params=project.params.resolve(overrides),
            params_meta={p.name: p.as_dict() for p in project.params},
            parts=parts, checks=run.as_dict(), manifest=dict(project.manifest),
            notes=notes,
            environment={"python": platform.python_version(),
                         "datum": _datum_version(), "material": material})

    # ---------------------------------------------------------------- файл
    def as_dict(self) -> dict:
        return {
            "tag": self.tag, "created": self.created, "project": self.project,
            "notes": self.notes, "environment": self.environment,
            "manifest": self.manifest,
            "params": {k: round(v, 6) for k, v in sorted(self.params.items())},
            "params_meta": self.params_meta,
            "parts": {n: p.as_dict() for n, p in sorted(self.parts.items())},
            "checks": self.checks,
        }

    def save(self, project_root: str | Path) -> Path:
        d = Path(project_root) / SNAPSHOT_DIR
        d.mkdir(parents=True, exist_ok=True)
        stamp = self.created.replace(":", "-").replace("+00-00", "Z")
        base = f"{stamp}__{_slug(self.tag)}"
        # Атомарно и без гонки за имя. Сначала — во временный файл: снимок,
        # оборванный посреди записи, иначе оставался бы битым JSON, и первая же
        # попытка перечислить снимки падала на нём. Затем — жёсткой ссылкой на
        # окончательное имя: она НЕ затирает существующий файл, поэтому два
        # снимка с одним тегом в одну секунду (окно и агент разом) получают
        # разные имена, а не переписывают друг друга.
        import os
        import tempfile

        fd, tmp = tempfile.mkstemp(prefix=".снимок-", suffix=".tmp", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.as_dict(), f, ensure_ascii=False, indent=2)
            n = 1
            while True:
                path = d / (f"{base}.json" if n == 1 else f"{base}-{n}.json")
                try:
                    os.link(tmp, path)
                    return path
                except FileExistsError:
                    n += 1
        finally:
            os.unlink(tmp)

    @staticmethod
    def load(path: str | Path) -> "Snapshot":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return Snapshot(
            tag=d["tag"], created=d["created"], project=d.get("project", ""),
            params=d.get("params", {}), params_meta=d.get("params_meta", {}),
            parts={n: PartMetrics.from_dict(p) for n, p in d.get("parts", {}).items()},
            checks=d.get("checks", {}), manifest=d.get("manifest", {}),
            notes=d.get("notes", ""), environment=d.get("environment", {}))

    # ---------------------------------------------------------------- отчёт
    def report(self) -> str:
        lines = [f"Снимок «{self.tag}» ({self.created}), проект {self.project}"]
        if self.notes:
            lines.append(f"  {self.notes}")
        lines.append("Детали:")
        for p in self.parts.values():
            if p.ok:
                e = p.extents
                mass = ("не печатается" if p.mass_by == "не печатается" else
                        f"{p.mass_g:.1f} г" + ("" if p.mass_by == "печать"
                                               else " (сплошным телом)"))
                lines.append(f"  {p.name}: {p.volume_mm3:.1f} мм³, "
                             f"{e[0]:.1f}×{e[1]:.1f}×{e[2]:.1f} мм, "
                             f"{mass}, тел {p.bodies}")
            else:
                lines.append(f"  {p.name}: СБОЙ — {p.error}")
        c = self.checks
        lines.append(f"Проверки: {'OK' if c.get('ok') else str(c.get('n_fail', 0)) + ' FAIL'} "
                     f"(прошло {c.get('n_pass', 0)})")
        blocking = self.params_meta_blocking()
        if blocking:
            lines.append(f"Не измерено: {', '.join(blocking)}")
        lines.append(f"Итого пластика: {self.total_mass_g:.1f} г"
                     + ("" if self.mass_by_print else
                        " — сплошным телом (объём × плотность): по настройкам печати меньше"))
        lines.append("Готов к печати: " + ("да" if self.print_ready else "нет"))
        return "\n".join(lines)


def _digest_of(project, part_name: str) -> str:
    from datum.build.part import source_digest

    return source_digest(project.parts.get(part_name))


def _code_digest_of(project, part_name: str) -> str:
    from datum.build.part import project_code_digest

    return project_code_digest(project.parts.get(part_name), project.root)


def _datum_version() -> str:
    """Версия из метаданных пакета, а не строкой в коде — та устаревала молча."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("datum")
    except PackageNotFoundError:                 # pragma: no cover — не установлен
        return "не установлен"


def _slug(text: str) -> str:
    out = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in text.strip())
    return out.strip("-") or "снимок"


def list_snapshots(project_root: str | Path) -> list[Path]:
    d = Path(project_root) / SNAPSHOT_DIR
    return sorted(d.glob("*.json")) if d.is_dir() else []


def snapshot_tag(path: Path) -> str:
    """Тег из имени файла снимка: «2026-09-21T13-06-12Z__v1.json» → «v1».

    Суффикс «-2», которым расходятся одноимённые снимки одной секунды, НЕ
    срезается: он часть имени, и законный тег «rev-2» иначе стал бы «rev».
    """
    return path.stem.split("__", 1)[1] if "__" in path.stem else path.stem


def pick_snapshot(files: list[Path], ref: str) -> Path:
    """Найти снимок по ссылке: номер с конца, точный тег или имя файла.

    Раньше ссылка искалась ПОДСТРОКОЙ, и брался последний из совпавших: при
    тегах `v1` и `v12` запрос `v1` находил `v12`, и `diff --a v1 --b v12`
    сравнивал снимок сам с собой, отвечая «без изменений». Теперь частичное
    совпадение годится, только если оно единственное.
    """
    ref = str(ref).strip()
    try:
        index = int(ref)
    except ValueError:
        index = None
    if index is not None:
        if not -len(files) <= index < len(files):
            raise KeyError(f"снимка №{ref} нет: их {len(files)}")
        return files[index]

    names = ", ".join(f.name for f in files)
    exact = [f for f in files if ref in (f.name, f.stem, snapshot_tag(f))
             or _slug(ref) == snapshot_tag(f)]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise KeyError(f"снимков с тегом {ref!r} несколько: "
                       + ", ".join(f.name for f in exact) + ". Укажите имя файла")
    partial = [f for f in files if ref in f.name]
    if len(partial) == 1:
        return partial[0]
    if partial:
        raise KeyError(f"{ref!r} подходит к нескольким снимкам: "
                       + ", ".join(f.name for f in partial) + ". Уточните")
    raise KeyError(f"нет снимка {ref!r}; есть: {names}")
