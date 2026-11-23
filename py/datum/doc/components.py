"""Общая библиотека покупного: замер, сделанный однажды, — для всех проектов.

Откуда (аудит 2026-11-21, §7.7). Мотор 5840-31ZY обмерен в проекте пресса и
понадобился в руке («размеры мотора и его вала используй из работы с
прессом»); MG996R, STS3215, подшипник 6203, M3 — в каждом проекте заново, и
каждый раз агент переписывал числа из чужого `params/base.yaml` руками.

Компонент — папка в `$XDG_DATA_HOME/datum/components/<имя>/`:

* `params.yaml` — параметры ТЕКСТОМ, как они записаны в проекте: с
  происхождением, источником и комментариями (в них живёт рассуждение);
* `files/…` — файлы проекта, которые с ними ходят: скрипт макета в `lib/`,
  таблица в `data/`;
* `component.json` — что это, откуда и когда сохранено.

В проект компонент приходит КОПИЕЙ с пометкой (решение владельца 2026-11-22),
а не ссылкой: проект остаётся самодостаточным, и архив `.datum` открывается на
другой машине. Обновление из библиотеки — повторным вызовом, по просьбе.

Библиотека — данные самого человека на его машине. Файлы `lib/` из неё — код,
который проект потом исполняет, как и любой свой скрипт; архив проекта
библиотеку не трогает ни в одну сторону.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from datum.doc.params_edit import EditError, param_text, write_atomically

__all__ = ["ComponentError", "Component", "library_root", "save", "load", "listing",
           "use", "FILE_ROOTS", "MAX_FILE_BYTES"]

#: Откуда файлы проекта можно класть в компонент: общий код и данные. Скрипты
#: деталей (`parts/`) в библиотеку не идут — деталь объявляет проект, а
#: компонент даёт ему параметры и функцию макета.
FILE_ROOTS = ("lib", "data")
MAX_FILE_BYTES = 5 * 1024 * 1024
_NAME = re.compile(r"[^\W_][\w.+\- ]{0,62}[^\W_]|[^\W_]", re.UNICODE)


class ComponentError(ValueError):
    """Компонент не сохранить или не взять — с причиной словами."""


@dataclass
class Component:
    name: str
    title: str = ""
    note: str = ""
    saved: str = ""                    # дата сохранения, ISO
    project: str = ""                  # из какого проекта
    params: dict[str, str] = field(default_factory=dict)      # имя → текст блока
    files: dict[str, bytes] = field(default_factory=dict)     # путь в проекте → содержимое

    def meta(self) -> dict:
        return {"name": self.name, "title": self.title, "note": self.note,
                "saved": self.saved, "project": self.project,
                "params": list(self.params), "files": sorted(self.files)}


def library_root(env=None) -> Path:
    from datum.doc.datahome import data_home

    return data_home(env) / "components"


def _check_name(name: str) -> str:
    name = str(name).strip()
    if not _NAME.fullmatch(name) or name in (".", ".."):
        raise ComponentError(
            f"имя компонента {name!r}: буквы, цифры, точка, плюс, дефис и пробел, до 64 "
            f"знаков, начинается и кончается буквой или цифрой (5840-31ZY, MG996R)")
    return name


def _project_file(root: Path, rel: str) -> Path:
    """Файл проекта, который можно положить в компонент, или отказ."""
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0] not in FILE_ROOTS:
        raise ComponentError(f"файл {rel!r}: в компонент идут только файлы из "
                             f"{', '.join(r + '/' for r in FILE_ROOTS)} проекта")
    path = root / p
    if path.is_symlink() or not path.is_file():
        raise ComponentError(f"файла {rel!r} в проекте нет (или это ссылка)")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ComponentError(f"файл {rel!r} больше {MAX_FILE_BYTES // 1024 // 1024} МБ — "
                             f"это не макет покупной детали")
    return path


def save(root: Path, name: str, *, params: list[str], files: tuple[str, ...] | list[str] = (),
         title: str = "", note: str = "", replace: bool = False,
         library: Path | None = None) -> Component:
    """Сохранить компонент из проекта `root`. Параметры — по именам, текстом."""
    from datum.doc.expr import CONSTANTS, FUNCTIONS, referenced_names
    from datum.doc.params import ParamRegistry

    name = _check_name(name)
    library = library or library_root()
    target = library / name
    if target.exists() and not replace:
        raise ComponentError(f"компонент {name!r} уже есть в библиотеке ({target}); "
                             f"заменить — replace=true")
    if not params and not files:
        raise ComponentError("пустой компонент: не названо ни параметров, ни файлов")
    text = (root / "params" / "base.yaml").read_text(encoding="utf-8") if params else ""
    blocks: dict[str, str] = {}
    for pname in params:
        try:
            blocks[pname] = param_text(text, pname)
        except EditError as e:
            raise ComponentError(str(e)) from e
    # Компонент обязан разбираться САМ: формула, читающая параметр, которого
    # в нём нет, в другом проекте упала бы на первой сборке.
    if blocks:
        import yaml

        try:
            raw = yaml.safe_load("\n".join(blocks.values())) or {}
        except yaml.YAMLError as e:
            raise ComponentError(f"параметры компонента не разбираются: {e}") from e
        known = set(blocks) | set(CONSTANTS) | set(FUNCTIONS)
        missing: dict[str, list[str]] = {}
        outside = False                              # формулы с параметрами материала
        for pname, body in raw.items():
            expr = body.get("expr") if isinstance(body, dict) else None
            if not expr:
                continue
            try:
                names = referenced_names(str(expr))
            except Exception as e:                   # noqa: BLE001 — причина словами
                raise ComponentError(f"формула {pname}: {e}") from e
            outside |= any(n.startswith("MAT_") for n in names)
            lost = sorted(n for n in names - known if not n.startswith("MAT_"))
            if lost:
                missing[str(pname)] = lost
        if missing:
            raise ComponentError(
                "формулы читают параметры, которых нет в компоненте: "
                + "; ".join(f"{k} ← {', '.join(v)}" for k, v in missing.items())
                + " — назовите и их, иначе в другом проекте компонент не разберётся")
        if not outside:
            # Формулы с параметрами материала вычислит проект: каталог — у него.
            try:
                ParamRegistry.loads("\n".join(blocks.values())).resolve()
            except Exception as e:                   # noqa: BLE001
                raise ComponentError(f"параметры компонента не вычисляются: {e}") from e
    data = {str(Path(rel)): _project_file(root, rel).read_bytes() for rel in files}
    comp = Component(name=name, title=title.strip(), note=note.strip(),
                     saved=_dt.date.today().isoformat(), project=_project_name(root),
                     params=blocks, files=data)
    library.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{name}.", dir=library))
    try:
        (tmp / "params.yaml").write_text(_params_file(comp), encoding="utf-8")
        for rel, content in data.items():
            dst = tmp / "files" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(content)
        (tmp / "component.json").write_text(
            json.dumps(comp.meta(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if target.exists():
            old = library / f".{name}.old"
            shutil.rmtree(old, ignore_errors=True)
            target.rename(old)
            tmp.rename(target)
            shutil.rmtree(old, ignore_errors=True)
        else:
            tmp.rename(target)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return comp


def _project_name(root: Path) -> str:
    try:
        import tomllib

        return str(tomllib.loads((root / "datum.toml").read_text(encoding="utf-8"))
                   .get("name") or root.name)
    except Exception:                                # noqa: BLE001 — имя — справка
        return root.name


def _params_file(comp: Component) -> str:
    head = (f"# Компонент {comp.name}" + (f": {comp.title}" if comp.title else "")
            + f"\n# сохранён {comp.saved} из проекта «{comp.project}»\n")
    return head + "".join(b if b.endswith("\n") else b + "\n" for b in comp.params.values())


def load(name: str, *, library: Path | None = None) -> Component:
    name = _check_name(name)
    library = library or library_root()
    folder = library / name
    meta_path = folder / "component.json"
    if not meta_path.is_file():
        have = [c["name"] for c in listing(library=library)]
        raise ComponentError(f"компонента {name!r} в библиотеке нет; есть: "
                             f"{', '.join(have) if have else 'ничего'} ({library})")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        text = (folder / "params.yaml").read_text(encoding="utf-8")
        params = {str(p): param_text(text, str(p)) for p in meta.get("params", [])}
        files = {}
        for rel in meta.get("files", []):
            p = Path(str(rel))
            if p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0] not in FILE_ROOTS:
                raise ComponentError(f"в компоненте {name!r} файл с путём {rel!r} — "
                                     f"такой в проект не кладётся")
            files[str(p)] = (folder / "files" / p).read_bytes()
    except ComponentError:
        raise
    except (OSError, ValueError, EditError) as e:
        raise ComponentError(f"компонент {name!r} испорчен: {e}") from e
    return Component(name=name, title=str(meta.get("title", "")), note=str(meta.get("note", "")),
                     saved=str(meta.get("saved", "")), project=str(meta.get("project", "")),
                     params=params, files=files)


def listing(*, library: Path | None = None) -> list[dict]:
    library = library or library_root()
    out = []
    if not library.is_dir():
        return out
    for folder in sorted(library.iterdir(), key=lambda p: p.name.lower()):
        meta_path = folder / "component.json"
        if folder.name.startswith(".") or not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out.append({"name": folder.name, "broken": True})
            continue
        out.append({"name": folder.name, "title": str(meta.get("title", "")),
                    "note": str(meta.get("note", "")), "saved": str(meta.get("saved", "")),
                    "project": str(meta.get("project", "")),
                    "params": list(meta.get("params", [])), "files": list(meta.get("files", []))})
    return out


@dataclass
class Used:
    added: list[str] = field(default_factory=list)          # параметры, дописанные в проект
    same: list[str] = field(default_factory=list)           # уже были с тем же значением
    differ: list[tuple[str, str, str]] = field(default_factory=list)   # имя, проект, библиотека
    files_written: list[str] = field(default_factory=list)
    files_same: list[str] = field(default_factory=list)
    text: str = ""                                           # новый текст params/base.yaml


def use(root: Path, comp: Component, check) -> Used:
    """Положить компонент в проект. `check(текст)` — разбор реестра целиком
    (его даёт операция, с параметрами материала проекта): не принял — на диск
    не попадает НИЧЕГО, ни параметры, ни файлы.

    Параметр, который в проекте уже есть, НЕ трогается: проект мог его
    перемерить. Отличие от библиотеки называется, решает человек. Файл,
    который в проекте есть и отличается, — отказ: молча затереть свой
    скрипт хуже, чем остановиться."""
    from datum.doc.params import ParamRegistry

    path = root / "params" / "base.yaml"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    out = Used()
    have = ParamRegistry.loads(text) if text.strip() else None
    mine = {p.name: p for p in have} if have is not None else {}
    theirs = {p.name: p for p in ParamRegistry.loads("\n".join(comp.params.values()))} \
        if comp.params else {}
    fresh: list[str] = []
    for pname, block in comp.params.items():
        if pname not in mine:
            fresh.append(block if block.endswith("\n") else block + "\n")
            out.added.append(pname)
            continue
        a, b = mine[pname].as_dict(), theirs[pname].as_dict()
        key = "expr" if "expr" in a or "expr" in b else "value"
        if a.get(key) == b.get(key) and a.get("unit") == b.get("unit"):
            out.same.append(pname)
        else:
            out.differ.append((pname, f"{a.get(key)} {a.get('unit', '')}".strip(),
                               f"{b.get(key)} {b.get('unit', '')}".strip()))
    clash = []
    for rel, content in comp.files.items():
        target = root / rel
        if target.is_symlink():
            clash.append(f"{rel} (в проекте это ссылка)")
        elif target.is_file():
            if target.read_bytes() == content:
                out.files_same.append(rel)
            else:
                clash.append(rel)
        else:
            out.files_written.append(rel)
    if clash:
        raise ComponentError(
            f"в проекте уже есть другие файлы с этими именами: {', '.join(clash)} — "
            f"компонент {comp.name!r} их не затирает. Сравните и уберите или переименуйте "
            f"свой файл; в проект не записано ничего")
    if fresh:
        mark = (f"# ── из библиотеки компонентов: {comp.name}"
                + (f" — {comp.title}" if comp.title else "")
                + f" (сохранён {comp.saved} из проекта «{comp.project}») ──\n")
        base = text if text.endswith("\n") or not text else text + "\n"
        out.text = base + ("\n" if base.strip() else "") + mark + "".join(fresh)
        check(out.text)
    for rel in out.files_written:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.tmp")
        tmp.write_bytes(comp.files[rel])
        os.replace(tmp, target)
    if fresh:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomically(path, out.text)
    return out
