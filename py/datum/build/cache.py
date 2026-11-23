"""Контент-адресуемый кеш сборок.

Две задачи. Первая — не пересобирать то, что не изменилось: на длинных цепочках
булевых операций это единственный способ держать отклик коротким. Вторая —
хранить крупные данные (меши, картинки) по отпечатку содержимого, чтобы снимок
версии и, позже, браузер ссылались на них, а не таскали байты.

О НАДЁЖНОСТИ КЛЮЧА. Ключ сборки складывается из отпечатка исходника детали,
версии ядра, аргументов и ЗНАЧЕНИЙ ТЕХ ПАРАМЕТРОВ, КОТОРЫЕ ДЕТАЛЬ РЕАЛЬНО
ПРОЧИТАЛА. Возникает вопрос: а если деталь ветвится по параметру и в разных
ветвях читает разное? Проверено опытом:

    @part("ветвление")
    def build():
        if P.MODE > 0: return box(0, P.A, ...)
        return box(0, P.B, ...)

    MODE=1 → прочитано ['A', 'MODE']
    MODE=0 → прочитано ['B', 'MODE']

Параметр ветвления ВСЕГДА оказывается среди прочитанных — иначе по нему нельзя
было бы ветвиться. Значит его значение входит в ключ, и путь исполнения ключом
задаётся однозначно. Устаревшее попадание невозможно.

ЧЕГО КЕШ НЕ ПРОЩАЕТ — недетерминированности. Деталь, использующая random, время
или чтение внешних файлов, при повторе даёт другой результат, и кеш молча отдаст
старый. Это проверено и тоже измерено. Поэтому: скрипт детали обязан быть
детерминирован, а режим `verify` пересобирает и сверяет отпечатки, чтобы такое
не жило годами незамеченным.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

__all__ = ["BuildCache", "CachedBuild", "KERNEL_VERSION", "solid_to_bytes",
           "solid_from_bytes"]

def _kernel_version() -> str:
    """Версия Datum и ПОЛНАЯ версия ядра.

    Раньше здесь стояла строка «manifold3d-3.5»: патч-выпуск ядра, изменивший
    триангуляцию, кеш не сбрасывал, и старые меши выдавались за новые.
    """
    from importlib.metadata import PackageNotFoundError, version

    def of(pkg: str) -> str:
        try:
            return version(pkg)
        except PackageNotFoundError:             # pragma: no cover
            return "?"

    return f"datum-{of('datum')}/manifold3d-{of('manifold3d')}"


#: Версия сериализации и ядра. Меняется — весь кеш сборок становится негодным.
KERNEL_VERSION = _kernel_version()

_MAGIC = b"DTMS"
_FORMAT_VERSION = 1


# ------------------------------------------------------------------ сериализация
def solid_to_bytes(solid) -> bytes:
    """Тело в компактный двоичный вид: заголовок, вершины float32, грани uint32."""
    from datum.geom.convert import vertices_faces

    verts, faces = vertices_faces(solid.manifold)
    v = np.ascontiguousarray(verts, dtype=np.float32)
    f = np.ascontiguousarray(faces, dtype=np.uint32)
    head = struct.pack("<4sIII", _MAGIC, _FORMAT_VERSION, len(v), len(f))
    return head + v.tobytes() + f.tobytes()


def solid_from_bytes(data: bytes, *, name: str = "") -> Any:
    """Обратно в Solid. Журнал операций не восстанавливается: у удачной сборки
    разбирать нечего, а у неудачной нечего и кешировать."""
    from datum.geom.convert import from_arrays
    from datum.geom.solid import Solid

    if len(data) < 16 or data[:4] != _MAGIC:
        raise ValueError("не тот формат: нет заголовка DTMS")
    _, version, nv, nf = struct.unpack("<4sIII", data[:16])
    if version != _FORMAT_VERSION:
        raise ValueError(f"формат кеша версии {version}, ожидается {_FORMAT_VERSION}")
    off = 16
    verts = np.frombuffer(data, dtype=np.float32, count=nv * 3, offset=off).reshape(nv, 3)
    off += nv * 3 * 4
    faces = np.frombuffer(data, dtype=np.uint32, count=nf * 3, offset=off).reshape(nf, 3)
    # Массивы np.frombuffer — только для чтения; `from_arrays` копирует их в
    # записываемые, иначе привязка manifold3d бросила бы TypeError.
    return Solid.wrap(from_arrays(verts, faces), name=name, op="cache")


# ------------------------------------------------------------------ записи
@dataclass(frozen=True, slots=True)
class CachedBuild:
    part: str
    key: str
    digest: str
    reads: dict[str, float]
    metrics: dict
    created: float

    def solid(self, cache: "BuildCache"):
        data = cache.get_blob(self.digest, "mesh")
        if data is None:
            return None
        return solid_from_bytes(data, name=self.part)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS objects (
    digest TEXT NOT NULL, ext TEXT NOT NULL, kind TEXT DEFAULT '',
    size INTEGER NOT NULL, created REAL NOT NULL, atime REAL NOT NULL,
    meta TEXT DEFAULT '', PRIMARY KEY (digest, ext));
CREATE TABLE IF NOT EXISTS builds (
    key TEXT PRIMARY KEY, part TEXT NOT NULL, source TEXT NOT NULL,
    digest TEXT NOT NULL, reads TEXT NOT NULL, metrics TEXT NOT NULL,
    created REAL NOT NULL, atime REAL NOT NULL);
CREATE TABLE IF NOT EXISTS part_reads (
    source TEXT NOT NULL, names TEXT NOT NULL, PRIMARY KEY (source, names));
CREATE INDEX IF NOT EXISTS builds_part ON builds(part);
CREATE INDEX IF NOT EXISTS objects_atime ON objects(atime);
"""


class BuildCache:
    """Кеш на файловой системе с указателем в sqlite."""

    def __init__(self, root: str | Path, *, budget_mb: int = 2048):
        self.root = Path(root)
        self.objects = self.root / "objects"
        self.objects.mkdir(parents=True, exist_ok=True)
        self.budget_mb = budget_mb
        self._db = sqlite3.connect(self.root / "index.sqlite", timeout=10.0,
                                   check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "BuildCache":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------ хранилище
    def _path(self, digest: str, ext: str) -> Path:
        return self.objects / digest[:2] / f"{digest}.{ext}"

    def put_blob(self, data: bytes, ext: str, *, kind: str = "",
                 meta: dict | None = None) -> str:
        digest = hashlib.sha256(data).hexdigest()
        path = self._path(digest, ext)
        if not path.exists():
            # Свой временный файл у каждого писателя. Общий `<объект>.tmp` у
            # двух процессов, кладущих один объект (окно и агент собирают одну
            # деталь), падал FileNotFoundError у второго, а первый мог урезать
            # уже переименованный файл (найдено обзором: 10 отказов из 10).
            # Объект по отпечатку содержимого один и тот же — кто положил
            # первым, тот и прав.
            import os
            import tempfile

            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=f".{digest[:12]}.", suffix=".tmp",
                                       dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                os.chmod(tmp, 0o644)
                os.replace(tmp, path)    # запись атомарна: битых объектов не бывает
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        now = time.time()
        self._db.execute(
            "INSERT INTO objects(digest, ext, kind, size, created, atime, meta) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(digest, ext) DO UPDATE SET atime=?",
            (digest, ext, kind, len(data), now, now,
             json.dumps(meta or {}, ensure_ascii=False), now))
        self._db.commit()
        return digest

    def get_blob(self, digest: str, ext: str) -> bytes | None:
        path = self._path(digest, ext)
        if not path.is_file():
            return None
        self._db.execute("UPDATE objects SET atime=? WHERE digest=? AND ext=?",
                         (time.time(), digest, ext))
        self._db.commit()
        return path.read_bytes()

    def blob_path(self, digest: str, ext: str) -> Path | None:
        path = self._path(digest, ext)
        return path if path.is_file() else None

    # ------------------------------------------------------------ ключ сборки
    @staticmethod
    def build_key(source: str, reads: dict[str, float], kwargs: dict) -> str:
        parts = [KERNEL_VERSION, source,
                 json.dumps(kwargs, sort_keys=True, default=repr),
                 ";".join(f"{n}={float(v)!r}" for n, v in sorted(reads.items()))]
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    def known_read_sets(self, source: str) -> list[tuple[str, ...]]:
        """Наборы прочитанных параметров, встречавшиеся у этого исходника.

        Их может быть несколько: деталь с ветвлением читает в разных ветвях
        разное. Проверяем все — совпадёт тот, чьи значения дают известный ключ.
        """
        rows = self._db.execute("SELECT names FROM part_reads WHERE source=?",
                                (source,)).fetchall()
        return [tuple(json.loads(r[0])) for r in rows]

    # ------------------------------------------------------------ поиск и запись
    def lookup(self, source: str, values: dict[str, float],
               kwargs: dict | None = None) -> CachedBuild | None:
        kwargs = kwargs or {}
        for names in self.known_read_sets(source):
            if not set(names) <= set(values):
                continue                 # параметр исчез из реестра — ключ не собрать
            reads = {n: values[n] for n in names}
            key = self.build_key(source, reads, kwargs)
            row = self._db.execute(
                "SELECT part, digest, reads, metrics, created FROM builds WHERE key=?",
                (key,)).fetchone()
            if row is None:
                continue
            if self.blob_path(row[1], "mesh") is None:
                self._db.execute("DELETE FROM builds WHERE key=?", (key,))
                self._db.commit()        # объект вычистили сборкой мусора — запись негодна
                continue
            self._db.execute("UPDATE builds SET atime=? WHERE key=?", (time.time(), key))
            self._db.commit()
            return CachedBuild(part=row[0], key=key, digest=row[1],
                               reads=json.loads(row[2]), metrics=json.loads(row[3]),
                               created=row[4])
        return None

    def store(self, part: str, source: str, reads: dict[str, float],
              kwargs: dict | None, solid, metrics: dict, *,
              data: bytes | None = None) -> CachedBuild:
        """`data` — уже готовые байты тела (`solid_to_bytes`), если вызывающий
        их посчитал для сверки: второй раз сетку не снимаем."""
        kwargs = kwargs or {}
        digest = self.put_blob(data if data is not None else solid_to_bytes(solid),
                               "mesh", kind="part", meta={"part": part})
        key = self.build_key(source, reads, kwargs)
        now = time.time()
        self._db.execute(
            "INSERT INTO builds(key, part, source, digest, reads, metrics, created, atime) "
            # Повторная запись под тем же ключом ОБНОВЛЯЕТ тело и метрики, а не
            # только время: иначе запись, однажды отвергнутая сверкой при чтении,
            # оставалась негодной навсегда, и деталь пересобиралась каждый раз.
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
            "atime=excluded.atime, digest=excluded.digest, metrics=excluded.metrics, "
            "created=excluded.created",
            (key, part, source, digest,
             json.dumps(reads, sort_keys=True), json.dumps(metrics, ensure_ascii=False),
             now, now))
        self._db.execute(
            "INSERT OR IGNORE INTO part_reads(source, names) VALUES(?,?)",
            (source, json.dumps(sorted(reads), ensure_ascii=False)))
        self._db.commit()
        return CachedBuild(part=part, key=key, digest=digest, reads=dict(reads),
                           metrics=metrics, created=now)

    # ------------------------------------------------------------ обслуживание
    def stats(self) -> dict:
        row = self._db.execute(
            "SELECT COUNT(*), COALESCE(SUM(size), 0) FROM objects").fetchone()
        builds = self._db.execute("SELECT COUNT(*) FROM builds").fetchone()[0]
        return {"objects": row[0], "bytes": row[1], "mb": round(row[1] / 1048576, 2),
                "builds": builds, "budget_mb": self.budget_mb}

    def over_budget(self) -> bool:
        """Кеш больше своего бюджета (один запрос к индексу, без обхода файлов)."""
        total = self._db.execute("SELECT COALESCE(SUM(size), 0) FROM objects").fetchone()[0]
        return total > self.budget_mb * 1048576

    def gc(self, budget_mb: int | None = None) -> dict:
        """Выбросить самые давно не используемые объекты до бюджета."""
        budget = (budget_mb if budget_mb is not None else self.budget_mb) * 1048576
        rows = self._db.execute(
            "SELECT digest, ext, size FROM objects ORDER BY atime ASC").fetchall()
        total = sum(r[2] for r in rows)
        removed, freed = 0, 0
        for digest, ext, size in rows:
            if total <= budget:
                break
            path = self._path(digest, ext)
            if path.is_file():
                path.unlink()
            self._db.execute("DELETE FROM objects WHERE digest=? AND ext=?", (digest, ext))
            self._db.execute("DELETE FROM builds WHERE digest=?", (digest,))
            total -= size
            removed += 1
            freed += size
        self._db.commit()
        return {"removed": removed, "freed_mb": round(freed / 1048576, 2),
                **self.stats()}

    def clear(self) -> None:
        for table in ("objects", "builds", "part_reads"):
            self._db.execute(f"DELETE FROM {table}")
        self._db.commit()
        for p in self.objects.rglob("*"):
            if p.is_file():
                p.unlink()
