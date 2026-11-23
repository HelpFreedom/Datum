"""Справочник API для агента — генерируется из кода, а не пишется руками.

Агент, пишущий скрипт детали, должен знать, ЧТО есть и С КАКОЙ ПОДПИСЬЮ, не
читая исходники. Справочник, написанный руками, отстаёт от кода с первой же
правки; поэтому здесь он собирается из `__all__`, подписей и первой строки
документации, а тест сверяет файл навыка с тем, что выдаёт `render()`. Новая
функция без документации, с переименованным аргументом или без раздела — и
тест падает с готовой командой обновления.

    .venv/bin/python -m datum.tools.apiref --write
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

from datum.build.hints import ALIASES, compact_signature

__all__ = ["render", "REFERENCE", "SECTIONS"]

REFERENCE = (Path(__file__).resolve().parents[3]
             / "plugin" / "skills" / "datum" / "references" / "api.md")

#: Разделы datum.geom: модуль → заголовок. Имя без раздела — ошибка генерации.
SECTIONS: tuple[tuple[str, str], ...] = (
    ("prims", "Тела"),
    ("profile2d", "Плоские профили (shapely)"),
    ("features", "Элементы для печати"),
    ("text", "Надписи"),
    ("booleans", "Булевы операции"),
    ("xform", "Перенос, поворот, размещение"),
    ("measure", "Измерения"),
    ("footprint", "По скану: тень, выравнивание"),
    ("sweep", "Карман по вставке"),
    ("solid", "Тело"),
    ("topology", "Топология и страж"),
    ("guard", "Топология и страж"),
    ("convert", "Обмен с trimesh"),
)

#: datum.build — то, что зовёт скрипт детали; остальное служебное.
FOR_SCRIPTS = ("part", "mechanism", "P", "bed", "bed_centre", "centre_on_bed", "draft",
               "imported", "project_root")

HEADER = """\
# Справочник API (генерируется)

Файл собирается из кода: `.venv/bin/python -m datum.tools.apiref --write`.
Руками не правится — тест сверяет его с кодом. Подпись — без аннотаций;
длины в мм, углы в градусах. Тело — `Solid`, неизменяемое:
`a + b` — объединение, `a - b` — вычитание, `a ^ b` — пересечение (не `&`).
Пакетные `union([…])`, `diff(a, […])` быстрее цепочки операторов.

Цилиндры и отверстия задаются ДИАМЕТРОМ и границами «от — до», а не высотой;
`box` — границами по осям, а не размером и центром. Профили — shapely
(`from shapely.geometry import box as sbox, Polygon`), выдавливаются
`extrude` / `slab` / `cup`.
"""


def _summary(obj) -> str:
    """Первый абзац документации — целиком, а не первая строка: строка
    обрывается на полуслове, если фраза перенесена. Длинный абзац — до конца
    первой фразы."""
    para = (inspect.getdoc(obj) or "").strip().split("\n\n")[0]
    text = " ".join(line.strip() for line in para.splitlines()).strip()
    cut = text.find(". ")
    return text[:cut + 1] if len(text) > 200 and cut >= 0 else text


#: Члены Solid, у которых своей документации нет (поля и свойства).
SOLID_MEMBERS = (
    ("name", "имя тела — им называется деталь в сообщениях проверок"),
    ("renamed(name)", "то же тело под другим именем"),
    ("digest()", ""),
    ("manifold", "Manifold из manifold3d — для того, чего в datum.geom нет"),
    ("to_trimesh()", "сетка trimesh — для анализа, не для построения"),
)


def _line(name: str, obj) -> str:
    doc = _summary(obj)
    if not doc:
        raise ValueError(f"у {name} нет документации — первая строка идёт в справочник")
    if inspect.isclass(obj):
        return f"- `{name}` — {doc}"
    if callable(obj):
        return f"- `{compact_signature(obj, name)}` — {doc}"
    return f"- `{name}` = {obj!r} — {doc}"


def render() -> str:
    import datum.build as build
    import datum.geom as geom
    from datum.geom import prims
    from datum.geom.solid import Solid

    by_section: dict[str, list[str]] = {}
    orphans: list[str] = []
    titles = dict(SECTIONS)
    for name in sorted(geom.__all__, key=str.lower):
        obj = getattr(geom, name)
        module = getattr(obj, "__module__", "") or ""
        short = module.rsplit(".", 1)[-1]
        if name == "Journal":                      # псевдоним типа, у него нет __module__
            by_section.setdefault(titles["solid"], []).append(
                "- `Journal` — журнал операций тела: кортеж `OpRecord`, от первой к последней")
            continue
        if name == "CIRC_N":
            line = (f"- `CIRC_N` = {prims.CIRC_N} — сегментов на полный оборот у "
                    f"цилиндров, конусов и шаров по умолчанию (`n=`)")
            by_section.setdefault(titles["prims"], []).append(line)
            continue
        if short not in titles:
            orphans.append(f"{name} ({module})")
            continue
        by_section.setdefault(titles[short], []).append(_line(name, obj))
    if orphans:
        raise ValueError(f"имена datum.geom без раздела в SECTIONS: {orphans}")

    out = [HEADER, "## datum.geom", ""]
    seen: list[str] = []
    for _, title in SECTIONS:
        if title in seen or title not in by_section:
            continue
        seen.append(title)
        out += [f"### {title}", "", *by_section[title], ""]

    out += ["### Методы Solid", ""]
    for call, doc in SOLID_MEMBERS:
        member = getattr(Solid, call.split("(")[0])          # нет члена — тест упадёт здесь
        doc = doc or _summary(member)
        out.append(f"- `тело.{call}` — {doc}")
    out.append("")

    out += ["## datum.build — для скрипта детали", ""]
    for name in FOR_SCRIPTS:
        if name == "P":
            out.append("- `P.ИМЯ` — значение параметра проекта (`P.WALL`); только внутри "
                       "сборки, чтение записывается в зависимости детали")
            continue
        out.append(_line(name, getattr(build, name)))
    rest = sorted(set(build.__all__) - set(FOR_SCRIPTS))
    out += ["", f"Служебное (скрипту не нужно): {', '.join(f'`{n}`' for n in rest)}.", ""]

    import datum.sim as sim
    from datum.sim.mechanism import Link

    out += ["## datum.sim — механизм для симуляции", "",
            "Объявляется под `@mechanism`; что и зачем — `references/simulation.md`.", ""]
    for name in ("revolute", "prismatic", "drive"):
        out.append(_line(name, getattr(sim, name)))
    out.append(_line("Mechanism", sim.Mechanism))
    for meth in ("link", "payload", "scenario"):
        out.append(_line(f"Mechanism.{meth}", getattr(sim.Mechanism, meth)))
    out.append(_line("Link.add", Link.add))
    out.append("")

    out += ["## Чужие названия → наши", "",
            "Ошибка импорта такого имени сама подскажет замену; здесь — всё сразу.", ""]
    for foreign, (_, how) in sorted(ALIASES.items()):
        out.append(f"- `{foreign}` → {how}")
    out.append("")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    text = render()
    if "--write" in argv:
        REFERENCE.write_text(text, encoding="utf-8")
        print(f"записано: {REFERENCE}")
        return 0
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
