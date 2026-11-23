"""Единицы приводов и движения."""

from __future__ import annotations

import pytest


# ------------------------------------------- приводы и движение (аудит 2026-11-21)
def test_torque_speed_and_time_units_for_drives():
    """Паспорт мотора записывался безразмерными числами: «Datum не знает
    единицы „Н·м“». Канон — то, что принимает drive(): Н·м, °/с, мм/с."""
    from datum.doc.units import CANONICAL, to_canonical, unit_kind

    assert unit_kind("Н·м") == "torque" and CANONICAL["torque"] == "N·m"
    assert to_canonical(70, "кгс·см") == pytest.approx(6.864655)       # 70 кгс·см в Н·м
    assert to_canonical(1500, "Н·мм") == pytest.approx(1.5)
    assert to_canonical(10, "об/мин") == pytest.approx(60.0)           # °/с
    assert to_canonical(1, "rad/s") == pytest.approx(57.29577951308232)
    assert to_canonical(0.5, "м/с") == pytest.approx(500.0)
    assert to_canonical(250, "мс") == pytest.approx(0.25)
    assert to_canonical(1600, "мА") == pytest.approx(1.6)
    with pytest.raises(ValueError):
        unit_kind("попугаи")


def test_a_motor_datasheet_can_be_a_parameter():
    from datum.doc.params import ParamRegistry

    reg = ParamRegistry.loads(
        'T_STALL: {value: 70, unit: "кгс·см", provenance: standard, ref: "паспорт 5840-31ZY"}\n'
        'SPEED: {value: 10, unit: "об/мин", provenance: standard, ref: "паспорт"}\n'
        'I_RATED: {value: 1.6, unit: A, provenance: standard, ref: "паспорт"}\n')
    values = reg.resolve()
    assert values["T_STALL"] == pytest.approx(6.864655) and values["SPEED"] == pytest.approx(60.0)
    assert reg["T_STALL"].display == pytest.approx(70.0)
