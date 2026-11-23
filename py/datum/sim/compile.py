"""Механизм → звенья с массой, центром масс и инерцией (СИ), без MuJoCo.

Отдельно от построения модели MuJoCo: здесь решается САМОЕ спорное — откуда
масса. Каждое тело звена помнит источник: печатная деталь с её настройками
печати (те же, что уйдут в слайсер) или покупное с известной массой.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from datum.sim.mechanism import Joint, Mechanism, MechanismError

__all__ = ["CompiledLink", "Compiled", "compile_mechanism", "combine"]

G_MM2_TO_KG_M2 = 1e-9


@dataclass
class CompiledLink:
    name: str
    parent: str | None
    joint: Joint | None
    mass_kg: float
    com_m: np.ndarray                    # в сцене при нуле
    inertia_kgm2: np.ndarray             # о центре масс, оси сцены
    pieces: list[dict] = field(default_factory=list)
    solids: list = field(default_factory=list)      # тела в сцене при нуле (Solid)


@dataclass
class Compiled:
    mechanism: Mechanism
    links: dict[str, CompiledLink]

    @property
    def total_mass_kg(self) -> float:
        return sum(lk.mass_kg for lk in self.links.values())


def combine(items: list[tuple[float, np.ndarray, np.ndarray]]) -> tuple[float, np.ndarray, np.ndarray]:
    """Сложить тела (масса, центр, инерция о своём центре) — теоремой Штейнера."""
    m = sum(it[0] for it in items)
    if m <= 0:
        raise MechanismError("суммарная масса звена — ноль")
    c = sum(it[0] * np.asarray(it[1], float) for it in items) / m
    inertia = np.zeros((3, 3))
    for mi, ci, ii in items:
        d = np.asarray(ci, float) - c
        inertia += np.asarray(ii, float) + mi * (float(d @ d) * np.eye(3) - np.outer(d, d))
    return m, c, inertia


def _apply(frame, com, inertia):
    if frame is None:
        return np.asarray(com, float), np.asarray(inertia, float)
    f = np.asarray(frame, float)
    r, t = f[:3, :3], f[:3, 3]
    return r @ np.asarray(com, float) + t, r @ np.asarray(inertia, float) @ r.T


def compile_mechanism(mech: Mechanism, *, part_settings: Callable, cache_dir,
                      on_piece: Callable[[str], None] | None = None) -> Compiled:
    """Звенья механизма с массами.

    `part_settings(имя детали)` → (PrintSettings, print_up) — даёт проект: так
    масса считается по тем же настройкам, что уйдут в слайсер.
    """
    from datum.fab.mass import cached_print_mass, uniform_props
    from datum.geom.xform import place

    mech.validate()
    out: dict[str, CompiledLink] = {}
    for name, lk in mech.links.items():
        items, pieces, solids = [], [], []
        for p in lk.pieces:
            if on_piece:
                on_piece(f"{name}: {p.label}")
            if p.part is not None and p.mass_g is not None:
                # печатная деталь с известной массой (весы, G-code): она важнее модели
                # слоёв; распределение — равномерное по телу
                m_g, com, inertia = uniform_props(p.solid, float(p.mass_g))
                src = "печать: масса задана (взвешено или из G-code)"
            elif p.part is not None:
                settings, up = part_settings(p.part)
                props = cached_print_mass(p.solid, settings, up=up, cache_dir=cache_dir)
                m_g, com, inertia = props.mass_g, np.array(props.com_mm), np.array(props.inertia_g_mm2)
                src = f"печать: {settings.describe()}"
            else:
                m_g, com, inertia = uniform_props(p.solid, float(p.mass_g))
                src = "покупное: масса задана"
            com, inertia = _apply(p.frame, com, inertia)
            items.append((m_g, com, inertia))
            pieces.append({"label": p.label, "part": p.part, "mass_g": round(m_g, 3), "source": src,
                           "com_mm": [round(float(v), 3) for v in com]})
            solids.append(p.solid if p.frame is None else place(p.solid, np.asarray(p.frame, float)))
        m_g, c_mm, i_gmm2 = combine(items)
        out[name] = CompiledLink(name=name, parent=lk.parent, joint=lk.joint,
                                 mass_kg=m_g / 1000.0, com_m=c_mm / 1000.0,
                                 inertia_kgm2=i_gmm2 * G_MM2_TO_KG_M2, pieces=pieces, solids=solids)
    return Compiled(mechanism=mech, links=out)
