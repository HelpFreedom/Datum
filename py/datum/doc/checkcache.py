"""Кеш результатов проверок — по тому, от чего проверка зависит.

Зачем. `check_run` пересчитывал ВСЁ при каждом вызове: кеш жил в памяти
процесса, а операция изолирована — каждый вызов в новом процессе. По журналам
сессий агента (аудит 2026-11-21): 150 вызовов, медиана 71 с, p90 143 с,
максимум 674 с — 181 минута ожидания; и почти всегда между вызовами менялась
пара параметров или не менялось ничего.

ЧТО ВХОДИТ В КЛЮЧ. Кеш проверок опаснее кеша сборок: устаревший ответ здесь —
это ложное «прошло». Поэтому ключ грубый там, где зависимость не проследить:

  * код Datum ЦЕЛИКОМ (все модули пакета и версии библиотек расчёта) —
    правка любой проверки, решателя или геометрии сбрасывает всё;
  * файлы проекта ЦЕЛИКОМ, кроме значений параметров и заметок: скрипты
    деталей, `lib/`, `checks/`, `assembly.yaml`, `placement.yaml`, `datum.toml`,
    модели, шрифты, любые свои файлы данных (`_files_digest`);
  * профиль слайсера, из которого берутся настройки печати (он ВНЕ проекта, а
    от него зависит масса звеньев в симуляции);
  * id проверки;
  * ЗНАЧЕНИЯ параметров, которые проверка прочитала — сама и через детали
    (`depends_on` результата; чтения деталей из кеша сборок туда входят).

Тонкая часть — последняя: правка одного параметра пересчитывает только
проверки, которые его читают. Набор прочитанного заранее неизвестен, поэтому,
как и у кеша сборок, для проверки помнятся наборы имён, встречавшиеся прежде,
и ключ пробуется по каждому.

ЧТО НЕ КЛАДЁТСЯ: результат с ошибкой (`error`) — она бывает преходящей
(нехватка памяти, неудачная сетка мешера); результат, который не
сериализуется. Доказательство-картинка обязана ещё лежать в кеше объектов —
иначе запись считается негодной.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import fields
from pathlib import Path
from typing import Any

from datum.checks.result import Artifact, CheckResult

__all__ = ["CheckCache", "datum_digest", "files_digest", "MAX_ROWS"]

#: Сколько записей держать; сверх — уходят давно не читанные.
MAX_ROWS = 20_000

#: Файлы проекта, которые в отпечаток НЕ входят: значения параметров (они в
#: ключе поимённо), заметки, выгрузка, служебное.
_SKIP_TOP = {".datum", "output", "snapshots", ".git", "__pycache__", ".claude"}
_SKIP_SUFFIX = (".md", ".pyc", ".swp", ".tmp", "~")

_DATUM_DIGEST: str | None = None


def datum_digest() -> str:
    """Отпечаток кода Datum и библиотек расчёта. Раз на процесс."""
    global _DATUM_DIGEST
    if _DATUM_DIGEST is None:
        from importlib.metadata import PackageNotFoundError, version

        import datum

        h = hashlib.sha256()
        root = Path(datum.__file__).resolve().parent
        for f in sorted(root.rglob("*.py")):
            if "__pycache__" in f.parts:
                continue
            h.update(str(f.relative_to(root)).encode("utf-8") + b"\0" + f.read_bytes() + b"\0")
        for package in ("manifold3d", "numpy", "scipy", "shapely", "trimesh", "scikit-fem",
                        "mujoco-core", "pypardiso", "fonttools", "uharfbuzz"):
            try:
                h.update(f"{package}={version(package)};".encode("ascii"))
            except PackageNotFoundError:
                h.update(f"{package}=?;".encode("ascii"))
        _DATUM_DIGEST = h.hexdigest()[:20]
    return _DATUM_DIGEST


def files_digest(root: Path) -> str:
    """Отпечаток файлов проекта (путь, размер, время правки), кроме значений
    параметров и заметок. По времени, а не по содержимому: тронутый файл —
    повод пересчитать, а читать гигабайт моделей на каждый прогон незачем."""
    root = Path(root)
    h = hashlib.sha256()
    stack = [root]
    found: list[tuple[str, int, int]] = []
    while stack:
        here = stack.pop()
        try:
            entries = sorted(here.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for path in entries:
            rel = path.relative_to(root)
            if rel.parts[0] in _SKIP_TOP or path.name == "__pycache__":
                continue
            if path.is_dir():
                if not path.is_symlink():
                    stack.append(path)
                continue
            if path.name.endswith(_SKIP_SUFFIX):
                continue
            if rel.parts[0] == "params" and path.suffix in (".yaml", ".yml"):
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            found.append((rel.as_posix(), st.st_size, st.st_mtime_ns))
    for item in sorted(found):
        h.update(repr(item).encode("utf-8"))
    return h.hexdigest()[:20]


def _to_dict(r: CheckResult) -> dict:
    d = {f.name: getattr(r, f.name) for f in fields(CheckResult) if f.name != "evidence"}
    d["depends_on"] = list(r.depends_on)
    d["inputs"] = list(r.inputs)
    if isinstance(r.threshold, tuple):
        d["threshold"] = {"tuple": list(r.threshold)}
    d["evidence"] = [{f.name: getattr(a, f.name) for f in fields(Artifact)}
                     for a in r.evidence]
    return d


def _from_dict(d: dict) -> CheckResult:
    d = dict(d)
    evidence = tuple(Artifact(**a) for a in d.pop("evidence", []))
    threshold = d.get("threshold")
    if isinstance(threshold, dict) and "tuple" in threshold:
        d["threshold"] = tuple(threshold["tuple"])
    d["depends_on"] = tuple(d.get("depends_on", ()))
    d["inputs"] = tuple(d.get("inputs", ()))
    return CheckResult(evidence=evidence, **d)


class CheckCache:
    def __init__(self, root: str | Path, *, context: str):
        """`context` — всё, что общее для прогона: код Datum, файлы проекта,
        профиль печати (собирает `Project`)."""
        self.root = Path(root)
        self.context = context
        folder = self.root / ".datum" / "cache"
        folder.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(folder / "checks.sqlite", timeout=30)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS results("
                         "key TEXT PRIMARY KEY, spec TEXT, results TEXT, created REAL, atime REAL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS reads("
                         "base TEXT, names TEXT, PRIMARY KEY(base, names))")
        self._db.commit()
        self.hits = 0
        self.misses = 0

    def _base(self, spec_id: str) -> str:
        return hashlib.sha256(f"{self.context}\0{spec_id}".encode("utf-8")).hexdigest()

    @staticmethod
    def _key(base: str, reads: dict[str, float]) -> str:
        body = json.dumps({k: repr(float(v)) for k, v in sorted(reads.items())},
                          ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(f"{base}\0{body}".encode("utf-8")).hexdigest()

    def lookup(self, spec_id: str, values: dict[str, float]) -> list[CheckResult] | None:
        base = self._base(spec_id)
        rows = self._db.execute("SELECT names FROM reads WHERE base=?", (base,)).fetchall()
        for (names_json,) in rows:
            names = json.loads(names_json)
            if not set(names) <= set(values):
                continue
            key = self._key(base, {n: values[n] for n in names})
            row = self._db.execute("SELECT results FROM results WHERE key=?", (key,)).fetchone()
            if row is None:
                continue
            try:
                results = [_from_dict(d) for d in json.loads(row[0])]
            except (ValueError, TypeError):
                self._db.execute("DELETE FROM results WHERE key=?", (key,))
                self._db.commit()
                continue
            # Картинка-доказательство обязана ещё лежать на месте: кеш объектов
            # чистится по давности, и ответ со ссылкой в пустоту хуже пересчёта.
            if any(a.path and not (self.root / a.path).is_file()
                   for r in results for a in r.evidence):
                self._db.execute("DELETE FROM results WHERE key=?", (key,))
                self._db.commit()
                continue
            self._db.execute("UPDATE results SET atime=? WHERE key=?", (time.time(), key))
            self._db.commit()
            self.hits += 1
            return results
        self.misses += 1
        return None

    def store(self, spec_id: str, reads: dict[str, float], results: list[CheckResult]) -> bool:
        """Запомнить. False — не запомнено (ошибка в результате или не сериализуется)."""
        if any(r.status == "error" for r in results):
            return False
        try:
            body = json.dumps([_to_dict(r) for r in results], ensure_ascii=False,
                              allow_nan=True)
        except (TypeError, ValueError):
            return False
        base = self._base(spec_id)
        key = self._key(base, reads)
        now = time.time()
        self._db.execute("INSERT OR REPLACE INTO results(key, spec, results, created, atime) "
                         "VALUES(?,?,?,?,?)", (key, spec_id, body, now, now))
        self._db.execute("INSERT OR IGNORE INTO reads(base, names) VALUES(?,?)",
                         (base, json.dumps(sorted(reads), ensure_ascii=False)))
        self._db.commit()
        return True

    def trim(self) -> int:
        """Убрать давно не читанное сверх `MAX_ROWS`."""
        total = self._db.execute("SELECT COUNT(*) FROM results").fetchone()[0]
        if total <= MAX_ROWS:
            return 0
        extra = total - MAX_ROWS
        self._db.execute("DELETE FROM results WHERE key IN (SELECT key FROM results "
                         "ORDER BY atime ASC LIMIT ?)", (extra,))
        self._db.commit()
        return extra

    def close(self) -> None:
        self._db.close()


def context_digest(*parts: Any) -> str:
    """Свести составляющие общего контекста в одну строку-отпечаток."""
    h = hashlib.sha256()
    for part in parts:
        h.update(json.dumps(part, ensure_ascii=False, sort_keys=True, default=str)
                 .encode("utf-8") + b"\0")
    return h.hexdigest()[:24]
