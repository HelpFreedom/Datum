"""Симуляция механизмов — MuJoCo поверх геометрии и масс Datum.

Объявления (`mechanism`) не тянут MuJoCo: проект открывается и без него.
MuJoCo импортирует только `sim.model` (держит test_layering). Что и почему
считается — в `sim.run`; откуда масса — в `sim.compile` и `datum.fab`.
"""

from datum.sim.mechanism import (BASES, Bearing, Capacity, Drive, Joint, Link, Mechanism,
                                 MechanismError, Payload, Piece, Scenario, bearing, capacity,
                                 drive, prismatic, revolute)

__all__ = ["Mechanism", "Link", "Piece", "Joint", "Drive", "Payload", "Scenario",
           "MechanismError", "BASES", "revolute", "prismatic", "drive", "Bearing", "bearing",
           "Capacity", "capacity"]
