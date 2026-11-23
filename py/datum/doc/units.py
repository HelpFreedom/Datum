"""Единицы. Внутри Datum всё хранится в мм, градусах, ньютонах, МПа, граммах
и г/см³; у приводов — Н·м, °/с, мм/с, секунды (см. ниже).

`unit` у параметра — это способ ВВОДА и ОТОБРАЖЕНИЯ, а не хранения. Набрал
`0.25in` — сохранилось 6.35 мм, показывается снова в дюймах. Так исключается
класс ошибок, на котором горят все CAD-обмены.
"""

from __future__ import annotations

import re

__all__ = ["CANONICAL", "to_canonical", "from_canonical", "parse_quantity", "unit_kind"]

#: коэффициент → канон, и к какому роду величины относится
_FACTORS: dict[str, tuple[float, str]] = {
    # длина → мм
    "mm": (1.0, "length"), "мм": (1.0, "length"),
    "cm": (10.0, "length"), "см": (10.0, "length"),
    "m": (1000.0, "length"), "м": (1000.0, "length"),
    "um": (0.001, "length"), "мкм": (0.001, "length"),
    "in": (25.4, "length"), "дюйм": (25.4, "length"),
    "mil": (0.0254, "length"),
    # угол → градусы
    "deg": (1.0, "angle"), "°": (1.0, "angle"), "град": (1.0, "angle"),
    "rad": (57.29577951308232, "angle"), "рад": (57.29577951308232, "angle"),
    # сила → ньютоны
    "N": (1.0, "force"), "Н": (1.0, "force"),
    "kgf": (9.80665, "force"), "кгс": (9.80665, "force"),
    # напряжение → МПа
    "MPa": (1.0, "stress"), "МПа": (1.0, "stress"),
    "Pa": (1e-6, "stress"), "Па": (1e-6, "stress"),
    "GPa": (1000.0, "stress"), "ГПа": (1000.0, "stress"),
    # масса → граммы
    "g": (1.0, "mass"), "г": (1.0, "mass"),
    "kg": (1000.0, "mass"), "кг": (1000.0, "mass"),
    # плотность → г/см³: так её пишут в паспорте филамента
    "g/cm3": (1.0, "density"), "г/см3": (1.0, "density"), "г/см³": (1.0, "density"),
    "kg/m3": (0.001, "density"), "кг/м3": (0.001, "density"),
    # ------------------------------------------------- приводы и движение
    # Появились вместе с симуляцией механизмов. Паспорт мотора («70 кгс·см,
    # 10 об/мин, 1.6 А») прежде записывался безразмерными числами — агент:
    # «Datum не знает единицы „Н·м“» — то есть ровно там, где уже обжигались
    # на единицах (градусы MjSpec, Н·м против Н·мм), реестр не защищал.
    #
    # Канон момента — Н·м, скорости вращения — °/с, линейной — мм/с: это то,
    # что принимает `drive(предел, скорость)`, поэтому параметр из паспорта
    # идёт в привод как есть. ВНИМАНИЕ: расчёты деталей машин
    # (`checks/machine.py`) ждут момент в Н·мм — туда параметр момента идёт
    # выражением `${expr: T * 1000}`.
    "N·m": (1.0, "torque"), "Н·м": (1.0, "torque"), "Nm": (1.0, "torque"),
    "N·mm": (0.001, "torque"), "Н·мм": (0.001, "torque"), "Nmm": (0.001, "torque"),
    "N·cm": (0.01, "torque"), "Н·см": (0.01, "torque"),
    "kgf·cm": (0.0980665, "torque"), "кгс·см": (0.0980665, "torque"),
    "kgf*cm": (0.0980665, "torque"), "kg·cm": (0.0980665, "torque"),
    "deg/s": (1.0, "angular_speed"), "°/s": (1.0, "angular_speed"),
    "град/с": (1.0, "angular_speed"), "°/с": (1.0, "angular_speed"),
    "rpm": (6.0, "angular_speed"), "об/мин": (6.0, "angular_speed"),
    "rad/s": (57.29577951308232, "angular_speed"),
    "рад/с": (57.29577951308232, "angular_speed"),
    "mm/s": (1.0, "speed"), "мм/с": (1.0, "speed"),
    "m/s": (1000.0, "speed"), "м/с": (1000.0, "speed"),
    "s": (1.0, "time"), "с": (1.0, "time"),
    "ms": (0.001, "time"), "мс": (0.001, "time"),
    "min": (60.0, "time"), "мин": (60.0, "time"),
    "N/mm": (1.0, "stiffness"), "Н/мм": (1.0, "stiffness"),
    "V": (1.0, "voltage"), "В": (1.0, "voltage"),
    "A": (1.0, "current"), "А": (1.0, "current"),
    "mA": (0.001, "current"), "мА": (0.001, "current"),
    "W": (1.0, "power"), "Вт": (1.0, "power"),
    # Только шкала Цельсия: перевод в кельвины — сдвиг, а не множитель.
    "°C": (1.0, "temperature"), "C": (1.0, "temperature"),
    # безразмерные
    "": (1.0, "scalar"), "-": (1.0, "scalar"), "шт": (1.0, "count"),
    "%": (0.01, "scalar"),
}

#: канонические единицы каждого рода
CANONICAL = {"length": "mm", "angle": "deg", "force": "N",
             "stress": "MPa", "mass": "g", "density": "g/cm3",
             "scalar": "", "count": "шт",
             "torque": "N·m", "angular_speed": "deg/s", "speed": "mm/s", "time": "s",
             "stiffness": "N/mm", "voltage": "V", "current": "A", "power": "W",
             "temperature": "°C"}

# Единица начинается не с цифры, но цифры в ней бывают: «g/cm3».
_QTY = re.compile(r"^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*([^\s\d]\S*)?\s*$")


def unit_kind(unit: str) -> str:
    if unit not in _FACTORS:
        raise ValueError(f"неизвестная единица {unit!r}; известны: {sorted(_FACTORS)}")
    return _FACTORS[unit][1]


def to_canonical(value: float, unit: str) -> float:
    if unit not in _FACTORS:
        raise ValueError(f"неизвестная единица {unit!r}")
    return float(value) * _FACTORS[unit][0]


def from_canonical(value: float, unit: str) -> float:
    if unit not in _FACTORS:
        raise ValueError(f"неизвестная единица {unit!r}")
    return float(value) / _FACTORS[unit][0]


def parse_quantity(text: str, default_unit: str = "mm") -> tuple[float, str]:
    """Разобрать «0.25in», «12 мм», «3.5» → (значение в каноне, исходная единица)."""
    m = _QTY.match(str(text))
    if not m:
        raise ValueError(f"не разобрать величину {text!r}")
    num, unit = m.group(1), (m.group(2) or default_unit)
    return to_canonical(float(num), unit), unit
