"""Опыт печати: что напечатали и чем кончилось — на все проекты.

Откуда (аудит 2026-11-21, §7.1). «Datum запомнит это для будущих проверок?» —
вопрос владельца про шестерни Gear Cube, вышедшие вершиной вниз с оплывшими
рёбрами. Урок тогда лёг абзацем в навык, где места на такие уроки нет. Проверки
знают физику, но не ЭТОТ принтер и ЭТОТ пластик; петли «печать → знание» у
Datum не было.

Урок — запись в общей базе (`lessons.json` в общих данных Datum, `doc/datahome.py`): проект,
деталь, пластик, итог, слова человека. У урока может быть **признак** — факт о
позе детали на столе из короткого списка ниже. Тогда сборка любой будущей
детали с тем же признаком напоминает об уроке.

Признак — ФАКТ, а не объяснение. Замер на Gear Cube показал, что ни одна мера
«нависаний» брак от чистой печати не отделяет, а причина не установлена;
поэтому признаков мало, они про позу («стоит на вершине»), и урок с признаком
принимается, только если признак выполняется на той самой детали, о которой
он записан. Напоминание — не проверка: вердикта оно не меняет.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

__all__ = ["Lesson", "LessonError", "SIGNS", "OUTCOMES", "path", "read", "add", "matching",
           "sign_holds", "describe_sign"]

OUTCOMES = ("плохо", "хорошо", "заметка")
MAX_TEXT = 600
#: Высота, на которой смотрится «острие»: первый слой и два миллиметра выше.
_FIRST_MM = 0.2
_ABOVE_MM = 2.0


class LessonError(ValueError):
    """Урок не записать — с причиной словами."""


@dataclass(frozen=True)
class Lesson:
    id: str
    date: str
    project: str
    part: str
    outcome: str
    text: str
    material: str = ""
    printer: str = ""
    print_up: str = ""
    sign: str = ""                                  # имя признака или пусто
    args: dict = field(default_factory=dict)

    def line(self) -> str:
        where = f"{self.project}, «{self.part}»" if self.part else self.project
        kit = ", ".join(v for v in (self.material, self.printer) if v)
        return (f"{self.date} [{self.outcome}] {where}" + (f" ({kit})" if kit else "")
                + f": {self.text}")


def path(env=None) -> Path:
    from datum.doc.datahome import data_home

    return data_home(env) / "lessons.json"


# ------------------------------------------------------------------ признаки
def _footprints(solid):
    """Сечения детали НА СТОЛЕ у самого низа и чуть выше: (у стола, выше, высота)."""
    from datum.geom.measure import bbox, section_xy

    lo, hi = bbox(solid)
    z0, height = float(lo[2]), float(hi[2] - lo[2])
    first = section_xy(solid, z0 + min(_FIRST_MM, height / 4))
    above = section_xy(solid, z0 + min(_ABOVE_MM, height / 2))
    return first, above, height


def _spread(shape) -> tuple[float, float]:
    """Меньшая и большая стороны наименьшего описанного прямоугольника."""
    if shape.is_empty:
        return 0.0, 0.0
    box = shape.minimum_rotated_rectangle
    if box.geom_type != "Polygon":
        return 0.0, float(box.length)
    xs, ys = box.exterior.coords.xy
    sides = sorted(((xs[i + 1] - xs[i]) ** 2 + (ys[i + 1] - ys[i]) ** 2) ** 0.5 for i in range(2))
    return float(sides[0]), float(sides[1])


def _vertex_down(solid, *, max_tip_mm2: float = 6.0) -> tuple[bool, str]:
    first, above, _ = _footprints(solid)
    small, big = _spread(above)
    holds = (first.area <= max_tip_mm2 and above.area >= 8 * max(first.area, 0.05)
             and small > 0 and big / small < 4)
    return holds, (f"у стола {first.area:.1f} мм², двумя миллиметрами выше — "
                   f"{above.area:.0f} мм² пятном {small:.1f}×{big:.1f} мм")


def _edge_down(solid, *, max_strip_mm: float = 2.0) -> tuple[bool, str]:
    first, above, _ = _footprints(solid)
    thin, long = _spread(first)
    wide, _ = _spread(above)
    holds = (not first.is_empty and thin <= max_strip_mm and long >= 6 * max(thin, 0.2)
             and wide >= 2.5 * max(thin, 0.2))
    return holds, (f"у стола полоска {thin:.1f}×{long:.1f} мм, двумя миллиметрами выше "
                   f"ширина {wide:.1f} мм")


def _small_footprint(solid, *, max_mm2: float = 100.0) -> tuple[bool, str]:
    first, _, _ = _footprints(solid)
    return first.area <= max_mm2, f"первый слой {first.area:.0f} мм² (порог урока {max_mm2:g})"


def _tall_thin(solid, *, ratio: float = 5.0) -> tuple[bool, str]:
    first, _, height = _footprints(solid)
    small, _ = _spread(first)
    got = height / small if small > 0 else float("inf")
    return got >= ratio, (f"высота {height:.0f} мм при опоре шириной {small:.1f} мм — "
                          f"в {got:.1f} раза (порог урока {ratio:g})")


#: Признак → (функция над телом В ПОЗЕ ПЕЧАТИ, что он значит, допустимые аргументы).
SIGNS: dict[str, tuple] = {
    "vertex_down": (_vertex_down, "деталь стоит на вершине (острием вниз)", ("max_tip_mm2",)),
    "edge_down": (_edge_down, "деталь стоит на ребре", ("max_strip_mm",)),
    "small_footprint": (_small_footprint, "первый слой меньше порога", ("max_mm2",)),
    "tall_thin": (_tall_thin, "высокая на узкой опоре", ("ratio",)),
}


def describe_sign(sign: str) -> str:
    return SIGNS[sign][1] if sign in SIGNS else sign


def _checked_args(sign: str, args: dict | None) -> dict:
    if sign not in SIGNS:
        raise LessonError(f"признака {sign!r} нет; есть: "
                          + "; ".join(f"{k} — {v[1]}" for k, v in SIGNS.items()))
    out = {}
    for key, value in (args or {}).items():
        if key not in SIGNS[sign][2]:
            raise LessonError(f"у признака {sign} нет аргумента {key!r}; есть: "
                              f"{', '.join(SIGNS[sign][2])}")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise LessonError(f"аргумент {key} признака {sign}: нужно число, "
                              f"получено {value!r}") from None
        if not (number > 0 and number < 1e6):
            raise LessonError(f"аргумент {key} признака {sign}: {number:g} — нужно "
                              f"положительное число")
        out[key] = number
    return out


def sign_holds(sign: str, args: dict | None, solid_on_bed) -> tuple[bool, str]:
    """Выполняется ли признак на теле в позе печати: (да/нет, что намерено)."""
    clean = _checked_args(sign, args)            # сначала отказ словами, потом счёт
    return SIGNS[sign][0](solid_on_bed, **clean)


# ---------------------------------------------------------------------- база
def read(*, file: Path | None = None) -> list[Lesson]:
    file = file or path()
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for d in raw if isinstance(raw, list) else []:
        try:
            out.append(Lesson(**{k: d[k] for k in Lesson.__dataclass_fields__ if k in d}))
        except TypeError:
            continue                              # чужая или испорченная запись — мимо
    return out


def add(*, project: str, part: str, outcome: str, text: str, material: str = "",
        printer: str = "", print_up: str = "", sign: str = "", args: dict | None = None,
        now: _dt.datetime | None = None, file: Path | None = None) -> Lesson:
    import fcntl

    from datum.doc.params_edit import write_atomically

    if outcome not in OUTCOMES:
        raise LessonError(f"итог {outcome!r}: один из {', '.join(OUTCOMES)}")
    flat = " ".join(str(text).split())
    if not flat:
        raise LessonError("пустой урок: что напечатали и чем кончилось — словами")
    if len(flat) > MAX_TEXT:
        raise LessonError(f"урок длиннее {MAX_TEXT} знаков ({len(flat)}): одна мысль — "
                          f"один урок; подробности — в NOTES.md проекта")
    clean = _checked_args(sign, args) if sign else {}
    moment = now or _dt.datetime.now()
    file = file or path()
    file.parent.mkdir(parents=True, exist_ok=True)
    # Базу пишут разные проекты и разные процессы: читать и писать — под замком.
    with open(file.with_name(file.name + ".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            have = read(file=file)
            lesson = Lesson(id=f"{moment:%Y%m%d-%H%M%S}-{len(have) + 1}",
                            date=f"{moment:%Y-%m-%d}", project=project, part=part,
                            outcome=outcome, text=flat, material=material, printer=printer,
                            print_up=print_up, sign=sign, args=clean)
            write_atomically(file, json.dumps([asdict(x) for x in [*have, lesson]],
                                              ensure_ascii=False, indent=1) + "\n")
            return lesson
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def matching(solid_on_bed, *, material: str = "", lessons: list[Lesson] | None = None
             ) -> list[tuple[Lesson, str]]:
    """Уроки с признаком, который выполняется на этом теле (в позе печати):
    (урок, что намерено). Только «плохо» и «заметка»: удачная печать — не
    предупреждение. Урок про другой пластик тоже напоминается — он назван в
    строке урока, а судит человек."""
    del material                                   # пластик — в строке урока, не фильтр
    out = []
    seen: dict[tuple, tuple[bool, str]] = {}
    for lesson in read() if lessons is None else lessons:
        if not lesson.sign or lesson.sign not in SIGNS or lesson.outcome == "хорошо":
            continue
        key = (lesson.sign, tuple(sorted(lesson.args.items())))
        if key not in seen:
            try:
                seen[key] = sign_holds(lesson.sign, lesson.args, solid_on_bed)
            except Exception:                      # noqa: BLE001 — напоминание, не отказ
                seen[key] = (False, "")
        holds, measured = seen[key]
        if holds:
            out.append((lesson, measured))
    return out
