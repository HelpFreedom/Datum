"""Проект одним файлом — архив `.datum`.

Рабочее место проекта — по-прежнему каталог: его правят агент и редактор, в
нём живёт git, в него сразу пишется каждая правка. Архив — то, что хранят,
пересылают и открывают снова: «Сохранить» упаковывает каталог в файл, из
которого он был открыт, «Сохранить как…» — в новый, «Открыть» распаковывает.

Устройство архива — zip:

    datum-archive.json   опись: формат, имя, когда, сколько файлов
    datum.toml, params/, parts/, lib/, checks/, assembly.yaml, imported/,
    snapshots/, NOTES.md, .claude/, .gitignore, .mcp.json …

В архив НЕ идут: кеш `.datum/`, выгрузка `output/` (их пересчитают),
`__pycache__`, `.git`, временные файлы редакторов и символьные ссылки — ссылка
утащила бы в архив файл вне проекта, а при распаковке указала бы куда угодно.

Распаковка — это чтение ЧУЖОГО файла, и охраняется соответственно: ни
абсолютных путей, ни `..`, ни ссылок; объём считается по реально прочитанным
байтам, а не по заголовкам zip, которым верить нельзя; распаковка идёт во
временный каталог и лишь затем переименовывается — полупроекта не бывает.

Где проект сохранён, помнит `.datum/archive.json`: путь к архиву, его
отпечаток и отпечаток файлов на момент сохранения. По последнему видно,
есть ли несохранённое, — это `stat` файлов, без чтения содержимого.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import stat
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

__all__ = ["ArchiveError", "SUFFIX", "MANIFEST", "pack", "unpack", "state",
           "fingerprint", "members"]

FORMAT = 1
SUFFIX = ".datum"
MANIFEST = "datum-archive.json"
STATE = Path(".datum") / "archive.json"

#: Каталоги, которые не упаковываются ни на какой глубине.
SKIP_DIRS = {"__pycache__", "node_modules"}
#: Верхний уровень: пересчитываемое и чужое проекту.
SKIP_TOP = {"output"}
#: Точечные имена, которым в архиве место: настройки агента и служебные файлы
#: проекта. Остальное точечное — кеш `.datum/`, `.git`, временные редакторов.
ALLOWED_DOT_TOP = {".claude", ".gitignore", ".mcp.json"}
#: Временные файлы редакторов.
EDITOR_TEMP = ("~", ".swp", ".swx", ".tmp")

#: Уже сжатое не пережимается: 3MF и PNG — это zip и deflate внутри.
STORED = {".3mf", ".png", ".jpg", ".jpeg", ".zip", ".glb", ".webp", ".gz"}

#: Пределы распаковки. Проект с импортированной моделью в полтора миллиона
#: треугольников — сотня мегабайт; 4 ГБ — с запасом, но не весь диск.
MAX_TOTAL_BYTES = 4 * 1024 ** 3
MAX_FILES = 50_000


class ArchiveError(RuntimeError):
    """Архив не записался или не распаковался — и почему."""


# ------------------------------------------------------------------ опись
def _skipped_name(rel: PurePosixPath) -> bool:
    parts = rel.parts
    if parts[0] in SKIP_TOP:
        return True
    for i, part in enumerate(parts):
        if part in SKIP_DIRS:
            return True
        if part.startswith(".") and not (i == 0 and part in ALLOWED_DOT_TOP):
            return True
    # Личные настройки агента (разрешения, пути этой машины) — не для передачи
    # ни в одну сторону.
    if parts[0] == ".claude" and rel.name == "settings.local.json":
        return True
    return rel.name.endswith(EDITOR_TEMP) or rel.name.startswith(".#")


def members(root: Path) -> tuple[list[Path], list[str]]:
    """Что пойдёт в архив (пути относительно корня) и что пропущено по ссылкам."""
    root = Path(root)
    files: list[Path] = []
    links: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        keep = []
        for d in sorted(dirnames):
            rel = PurePosixPath((here / d).relative_to(root).as_posix())
            if (here / d).is_symlink():
                links.append(str(rel))
            elif not _skipped_name(rel):
                keep.append(d)
        dirnames[:] = keep
        for f in sorted(filenames):
            path = here / f
            rel = PurePosixPath(path.relative_to(root).as_posix())
            if _skipped_name(rel):
                continue
            if path.is_symlink():
                links.append(str(rel))
            elif path.is_file():
                files.append(Path(rel))
    return files, links


def fingerprint(root: Path) -> str:
    """Отпечаток состава и времён файлов проекта: изменилось ли что-то с сохранения."""
    root = Path(root)
    h = hashlib.sha256()
    files, _ = members(root)
    for rel in files:
        st = (root / rel).stat()
        h.update(f"{rel.as_posix()}\0{st.st_size}\0{st.st_mtime_ns}\n".encode("utf-8"))
    return h.hexdigest()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _remember(root: Path, archive: Path) -> None:
    target = Path(root) / STATE
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {"path": str(archive), "sha256": _sha256(archive), "saved": _now(),
            "fingerprint": fingerprint(root)}
    from datum.doc.params_edit import write_atomically

    write_atomically(target, json.dumps(data, ensure_ascii=False, indent=2))


def state(root: Path) -> dict | None:
    """Где проект сохранён и есть ли несохранённое. None — ещё не сохранялся."""
    try:
        data = json.loads((Path(root) / STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    path = Path(data.get("path", ""))
    return {"path": str(path), "name": path.name, "saved": data.get("saved", ""),
            "exists": path.is_file(),
            "dirty": data.get("fingerprint") != fingerprint(root)}


def _is_archive(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            return MANIFEST in z.namelist()
    except (OSError, zipfile.BadZipFile):
        return False


# --------------------------------------------------------------- упаковка
@dataclass(frozen=True, slots=True)
class Packed:
    path: Path
    files: int
    bytes: int
    links: tuple[str, ...]


def pack(root: str | Path, dest: str | Path, *, overwrite: bool = False) -> Packed:
    """Упаковать проект в архив `dest` (расширение `.datum` добавится само)."""
    root = Path(root).resolve()
    if not (root / "datum.toml").is_file():
        raise ArchiveError(f"{root} — не проект Datum: нет datum.toml")
    dest = Path(dest).expanduser()
    if dest.suffix.lower() != SUFFIX:
        dest = dest.with_name(dest.name + SUFFIX)
    dest = dest.resolve()
    if dest.is_relative_to(root):
        raise ArchiveError(f"архив внутри самого проекта ({dest}) попал бы в следующий "
                           f"архив — сохраните его рядом с проектом или в другое место")
    if dest.exists():
        if not overwrite:
            raise ArchiveError(f"{dest} уже есть — перезаписать можно только явно")
        # Затирается только архив Datum: операцию зовёт и агент, и путь с
        # ошибкой не должен стоить человеку чужого файла.
        if not dest.is_file() or not _is_archive(dest):
            raise ArchiveError(f"{dest} — не архив Datum, перезаписывать его нельзя")
    if not dest.parent.is_dir():
        raise ArchiveError(f"нет каталога {dest.parent}")

    files, links = members(root)
    manifest = {"format": FORMAT, "name": root.name, "created": _now(), "files": len(files)}
    fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.stem}.", suffix=".tmp", dir=dest.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6) as z:
            z.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
            for rel in files:
                how = (zipfile.ZIP_STORED if rel.suffix.lower() in STORED
                       else zipfile.ZIP_DEFLATED)
                z.write(root / rel, rel.as_posix(), compress_type=how)
        # mkstemp создаёт файл 0600; архив — обычный файл человека.
        os.chmod(tmp, 0o666 & ~_umask())
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _remember(root, dest)
    return Packed(dest, len(files), dest.stat().st_size, tuple(links))


# ------------------------------------------------------------- распаковка
def _safe_member(info: zipfile.ZipInfo) -> PurePosixPath | None:
    """Путь члена архива внутри проекта — или отказ. None — каталог, пропустить."""
    name = info.filename
    if name.endswith("/"):
        return None
    if "\\" in name or name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        raise ArchiveError(f"в архиве путь вне проекта: {name!r}")
    rel = PurePosixPath(name)
    if any(part in ("", ".", "..") for part in rel.parts):
        raise ArchiveError(f"в архиве путь вне проекта: {name!r}")
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        raise ArchiveError(f"в архиве символьная ссылка: {name!r} — такие не распаковываются")
    return rel


def _free_name(parent: Path, stem: str) -> Path:
    candidate = parent / stem
    n = 2
    while candidate.exists():
        if n > 999:                                        # pragma: no cover
            raise ArchiveError(f"рядом с архивом слишком много папок {stem!r}")
        candidate = parent / f"{stem} ({n})"
        n += 1
    return candidate


def _refresh_mcp(root: Path) -> None:
    """`.mcp.json` из init указывает на интерпретатор той машины, где проект
    заводили. На этой он другой — иначе `claude` в каталоге не увидел бы
    инструментов. Правится только файл ровно того вида, что пишет init."""
    path = root / ".mcp.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        server = data["mcpServers"]["datum"]
    except (OSError, ValueError, KeyError, TypeError):
        return
    if server.get("args") != ["-m", "datum.cli", "mcp", "--stdio"]:
        return
    if server.get("command") == sys.executable:
        return                               # та же машина — файл как был
    server["command"] = sys.executable
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@dataclass(frozen=True, slots=True)
class Unpacked:
    project: Path
    reused: bool
    files: int
    skipped: int = 0          # служебное в архиве, которое не распаковано


def unpack(archive: str | Path, into: str | Path | None = None) -> Unpacked:
    """Распаковать архив в новый каталог и вернуть его.

    Без `into` — рядом с архивом, в каталог по имени архива. Если там уже
    лежит распакованный ИМЕННО ЭТОТ архив (тот же отпечаток), он и
    открывается: там могут быть правки после распаковки, и затирать или
    дублировать их незачем. Иначе берётся свободное имя «имя (2)».
    """
    archive = Path(archive).expanduser().resolve()
    if not archive.is_file():
        raise ArchiveError(f"нет такого файла: {archive}")
    try:
        z = zipfile.ZipFile(archive)
    except (OSError, zipfile.BadZipFile) as e:
        raise ArchiveError(f"{archive.name} — не архив Datum: {e}") from e
    with z:
        names = z.namelist()
        if MANIFEST not in names:
            raise ArchiveError(f"{archive.name} — не архив Datum: нет описи {MANIFEST}")
        try:
            manifest = json.loads(z.read(MANIFEST).decode("utf-8"))
            version = int(manifest.get("format", 0))
        except (ValueError, TypeError, AttributeError) as e:
            raise ArchiveError(f"{archive.name}: опись архива не читается — {e}") from e
        if version > FORMAT:
            raise ArchiveError(f"{archive.name} записан более новой версией Datum "
                               f"(формат {version}, эта понимает до {FORMAT})")
        if "datum.toml" not in names:
            raise ArchiveError(f"{archive.name}: в архиве нет datum.toml — это не проект")
        if len(names) > MAX_FILES:
            raise ArchiveError(f"{archive.name}: {len(names)} файлов — больше предела {MAX_FILES}")
        plan = [(info, _safe_member(info)) for info in z.infolist()
                if info.filename != MANIFEST]
        # Распаковывается только то, что упаковка могла положить. `pack`
        # служебное пропускает (`.git`, `.datum`, точечные имена, `output/`),
        # а чужой архив мог его нести: `.git/config` исполняет первый же
        # `git status`, запись в `.datum/trash/…/meta.json` уводила «Вернуть»
        # за пределы проекта, подложенный `.datum/cache` подменял геометрию
        # (аудит 2026-11-21, воспроизведено). Не отказ, а пропуск: архив,
        # собранный руками вместе с `.git`, должен открываться.
        skipped = sum(1 for _, rel in plan if rel is not None and _skipped_name(rel))
        plan = [(info, rel) for info, rel in plan
                if rel is None or not _skipped_name(rel)]

        digest = _sha256(archive)
        if into is None:
            target = archive.parent / archive.stem
            known = state(target) if target.is_dir() else None
            if known and Path(known["path"]) == archive:
                try:
                    recorded = json.loads((target / STATE).read_text(encoding="utf-8"))
                except (OSError, ValueError):               # pragma: no cover
                    recorded = {}
                if recorded.get("sha256") == digest:
                    return Unpacked(target, True, 0)
            target = _free_name(archive.parent, archive.stem)
        else:
            target = Path(into).expanduser().resolve()
            if target.exists():
                raise ArchiveError(f"{target} уже есть — в существующий каталог архив "
                                   f"не распаковывается")

        staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
        try:
            total = 0
            count = 0
            for info, rel in plan:
                if rel is None:
                    continue
                out = staging / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(out, "xb") as dst:
                    for chunk in iter(lambda: src.read(1 << 20), b""):
                        total += len(chunk)
                        if total > MAX_TOTAL_BYTES:
                            raise ArchiveError(
                                f"{archive.name}: распакованное больше "
                                f"{MAX_TOTAL_BYTES // 1024 ** 3} ГБ — похоже на zip-бомбу")
                        dst.write(chunk)
                count += 1
            os.chmod(staging, 0o777 & ~_umask())
            os.rename(staging, target)
        except BaseException:
            import shutil

            shutil.rmtree(staging, ignore_errors=True)
            raise
    _refresh_mcp(target)
    _remember(target, archive)
    return Unpacked(target, False, count, skipped)


def _umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask
