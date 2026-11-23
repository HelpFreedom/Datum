"""Чужие модели как детали проекта.

Импортированная модель живёт в `imported/<имя>/`:

    source.<ext>   исходник как есть, нетронутый
    meta.json      что с ним сделали: отпечаток, шаги ремонта, когда
    result.stl     готовый к работе меш

Почему исходник хранится отдельно и не трогается: ремонт — это решение, а не
факт. Через месяц надо уметь ответить «что именно мы сделали с чужой моделью»,
и сравнить с оригиналом. Журнал в `meta.json` позволяет и переиграть ремонт с
нуля.

Скрипт детали берёт результат одной строкой:

    from datum.build import imported, part

    @part("корпус телефона")
    def build():
        return imported("redmi")
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

__all__ = ["imported", "imported_dir", "imported_names", "imported_stamp",
           "ImportedError", "NAME_RE", "used_by", "part_file_stem", "part_source",
           "data_stamp", "DATA_DIR"]

#: Имя импортированной модели. Нарочно узкое: оно становится именем каталога, и
#: ни косых черт, ни точек в нём быть не должно.
NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9_-]{0,63}$")

RESULT_SUFFIXES = (".stl", ".3mf", ".obj", ".ply")


class ImportedError(RuntimeError):
    """Чужая модель не найдена или негодна."""


def imported_dir(root: str | Path) -> Path:
    return Path(root) / "imported"


def imported_names(root: str | Path) -> list[str]:
    """Имена моделей в imported/ проекта."""
    base = imported_dir(root)
    if not base.is_dir():
        return []
    # Каталоги с точки — незавершённый импорт или снятая прежняя версия
    # (см. `import_model`): моделями проекта они не являются.
    return sorted(d.name for d in base.iterdir()
                  if d.is_dir() and not d.name.startswith(".")
                  and (d / "meta.json").is_file())


def result_path(root: str | Path, name: str) -> Path | None:
    folder = imported_dir(root) / name
    for suffix in RESULT_SUFFIXES:
        candidate = folder / f"result{suffix}"
        if candidate.is_file():
            return candidate
    return None


#: Каталог проекта для своих файлов данных, которые читает скрипт детали.
DATA_DIR = "data"
#: Больше этого файл в `data/` не хешируется содержимым — только размер и время.
_DATA_HASH_MAX = 8 * 1024 * 1024


def data_stamp(root: str | Path) -> str:
    """Отпечаток каталога `data/` проекта — в ключ кеша сборок.

    Перила на запрос сети отвечают «положите данные в проект и читайте из
    файла» — и это верный совет, но файл данных в ключ кеша не входил: правка
    таблицы отдавала вчерашнюю геометрию (аудит 2026-11-21). Теперь у таких
    файлов есть место, и оно в ключе; файл, прочитанный из другого места
    проекта, кеш по-прежнему не видит — об этом говорит подсказка перил.
    Грубо — по всему каталогу сразу, как и у моделей (`imported_stamp`).
    """
    import hashlib

    base = Path(root) / DATA_DIR
    if not base.is_dir():
        return ""
    h = hashlib.sha256()
    seen = False
    for path in sorted(p for p in base.rglob("*") if p.is_file()):
        if path.name.startswith(".") or "__pycache__" in path.parts:
            continue
        try:
            st = path.stat()
            h.update(path.relative_to(base).as_posix().encode("utf-8") + b"\0")
            if st.st_size <= _DATA_HASH_MAX:
                h.update(path.read_bytes() + b"\0")
            else:
                h.update(f"{st.st_size}:{st.st_mtime_ns}".encode("ascii") + b"\0")
        except OSError:
            continue
        seen = True
    return h.hexdigest()[:16] if seen else ""


def imported_stamp(root: str | Path) -> str:
    """Отпечаток всего, что импортировано в проект.

    Зачем он нужен. Ключ кеша сборок состоит из отпечатка кода детали и
    значений прочитанных параметров. Чужой файл в него не входит: подменили
    модель — кеш этого не заметил бы и показал вчерашнюю геометрию. Это ровно
    тот род молчаливой ошибки, ради которого Datum и затевался.

    Отпечаток берётся грубо — по ВСЕМ импортированным моделям сразу, а не по
    тем, что читает конкретная деталь. Точнее было бы нельзя без той же
    механики «известных наборов чтений», что заведена для параметров, а
    выигрыш мнимый: импорт случается раз, а не на каждой правке.
    """
    base = imported_dir(root)
    if not base.is_dir():
        return ""
    parts = []
    for folder in sorted(base.iterdir()):
        meta = folder / "meta.json"
        if folder.name.startswith(".") or not meta.is_file():
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):               # pragma: no cover
            data = {}
        parts.append(f"{folder.name}={data.get('digest', '?')}")
    return hashlib.sha256(";".join(parts).encode("utf-8")).hexdigest()[:16]


def imported(name: str, *, root: str | Path | None = None):
    """Чужая модель как `Solid`.

    Путь не принимается намеренно: берётся ИМЯ, а каталог известен из контекста
    сборки. Иначе скрипт детали получил бы произвольный доступ к диску, а
    проект перестал бы быть самодостаточным.
    """
    from datum.build.context import project_root
    from datum.geom.convert import from_trimesh
    from datum.geom.repair import load_mesh
    from datum.geom.solid import Solid

    if not NAME_RE.match(name or ""):
        raise ImportedError(
            f"негодное имя модели {name!r}: допустимы буквы, цифры, дефис и "
            f"подчёркивание, до 64 знаков")

    base = Path(root) if root is not None else project_root()
    path = result_path(base, name)
    if path is None:
        have = imported_names(base)
        raise ImportedError(
            f"модель {name!r} не импортирована"
            + (f". Есть: {', '.join(have)}" if have else
               ". В проекте пока нет ни одной импортированной модели"))

    mesh = load_mesh(path)
    man = from_trimesh(mesh)
    return Solid.wrap(man, name=name, op="imported",
                      kwargs={"name": name, "file": path.name,
                              "triangles": int(len(mesh.faces))})


# ------------------------------------------------------------- модель → деталь
def used_by(root: str | Path, name: str) -> list[str]:
    """Файлы проекта, которые берут модель: `parts/*.py` и `lib/**/*.py`.

    Поиск по тексту `imported("имя")`, а не по журналу сборки: журнал знает
    только о собранном, а образец на столе нужно прятать и у детали, которая
    ещё ни разу не собиралась. Ложное срабатывание (имя в комментарии) лишь
    спрячет образец — показать его можно глазом в панели.
    """
    base = Path(root)
    pattern = re.compile(r"""imported\(\s*(['"])""" + re.escape(name) + r"""\1""")
    found = []
    for folder in ("parts", "lib"):
        top = base / folder
        if not top.is_dir():
            continue
        for path in sorted(top.rglob("*.py")):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):         # pragma: no cover
                continue
            if pattern.search(text):
                found.append(path.relative_to(base).as_posix())
    return found


#: Кириллица → латиница для имени файла детали. Имена файлов — латиницей, как и
#: идентификаторы (правило проекта про границу байтов); деталь при этом
#: называется по-русски, как модель.
_TRANSLIT = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "i", "k", "l", "m", "n",
     "o", "p", "r", "s", "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "",
     "e", "yu", "ya"]))


def part_file_stem(name: str) -> str:
    """Имя файла детали из имени модели: `Desk-Organizer` → `desk_organizer`."""
    latin = "".join(_TRANSLIT.get(ch, ch) for ch in name.lower())
    stem = re.sub(r"[^a-z0-9]+", "_", latin).strip("_")
    if not stem:
        stem = "model"
    if stem[0].isdigit():
        stem = f"m_{stem}"
    return stem


def part_source(name: str, part: str, *, at: tuple[float, float] = (0.0, 0.0),
                bodies: int = 1, voids: bool = False) -> str:
    """Скрипт детали из импортированной модели — поставленной на стол.

    Начало координат у чужих файлов бывает где угодно: у экспорта из слайсера —
    в центре его стола, у модели с Thingiverse — в углу или в середине детали.
    Поэтому деталь ставится по габариту: центр — в центр стола со сдвигом `at`
    (так импорт расставляет несколько объектов), низ — на стол. Сдвиг — от
    центра стола, а не от угла: сменили стол — расстановка поедет вместе с ним.

    bodies > 1 или voids — модель такой пришла из файла (объект из нескольких
    частей, полость внутри): страж детали это допускает, иначе деталь не
    собралась бы вовсе, а импорт свои замечания уже назвал.
    """
    # json.dumps — строка в двойных кавычках, как везде в проекте, и заодно
    # корректный литерал Python для любого допустимого имени.
    part_lit, name_lit = json.dumps(part, ensure_ascii=False), json.dumps(name, ensure_ascii=False)
    ox, oy = (round(float(v), 3) + 0.0 for v in at)      # +0.0: без «-0.0» в тексте
    expect = []
    if bodies > 1:
        expect.append("bodies=None")
    if voids:
        expect.append("allow_sealed_voids=True")
    decorator = (f"@part({part_lit}, expect=Expect({', '.join(expect)}))" if expect
                 else f"@part({part_lit})")
    imports = ("from datum.build import bed, imported, part\n"
               f"from datum.geom import {'Expect, ' if expect else ''}bbox, move\n")
    why = []
    if bodies > 1:
        why.append(f"модель из {bodies} отдельных тел — так она пришла из файла")
    if voids:
        why.append("в модели запаянные полости — так она пришла из файла")
    note = "".join(f"\n{w[0].upper()}{w[1:]}: страж это допускает." for w in why)
    where = ("в центре стола" if (ox, oy) == (0.0, 0.0)
             else f"на столе со сдвигом ({ox:g}, {oy:g}) мм от центра")
    shift = "" if (ox, oy) == (0.0, 0.0) else f"\nOX, OY = {ox!r}, {oy!r}                # сдвиг от центра стола, мм\n"
    dx = "bx / 2 - (x0 + x1) / 2" + (" + OX" if shift else "")
    dy = "by / 2 - (y0 + y1) / 2" + (" + OY" if shift else "")
    return f'''"""{part}: деталь из импортированной модели «{name}» (imported/{name}/).

Модель стоит {where}
и опущена на него. Что с ней делать дальше — вычесть из своей детали,
обрезать, повернуть — пишется здесь же.{note}
"""

{imports}{shift}

{decorator}
def build():
    model = imported({name_lit})
    (x0, y0, z0), (x1, y1, _) = bbox(model)
    bx, by, _ = bed()
    return move(model, dx={dx}, dy={dy}, dz=-z0)
'''
