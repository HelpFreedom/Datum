"""URDF механизма — текстом, без сторонних библиотек.

Зачем. Кинематику руки-v3 агент переносил в прошивку руками (аудит
2026-11-21, §7.6): звенья, оси, пределы. URDF читают ROS, PyBullet, Isaac,
Pinocchio и сам MuJoCo — им же записанное и сверяется (`sim/model.py`).

Системы координат. В Datum рамки всех звеньев при нуле совпадают со сценой.
URDF требует, чтобы ось сустава проходила через начало рамки ребёнка, поэтому
рамка звена здесь — оси сцены, перенесённые в точку на оси его сустава
(у основания и у ползуна — в начало сцены). Отсюда все смещения ниже:
сустав — разность точек на осях, центр масс и сетка — минус точка своей оси.

Единицы — СИ (метры, радианы, килограммы), как требует URDF; сетки остаются
в миллиметрах и масштабируются `scale="0.001 …"`.
"""

from __future__ import annotations

import math
from xml.sax.saxutils import quoteattr

import numpy as np

from datum.sim.compile import Compiled

__all__ = ["urdf_text", "link_origins"]


def link_origins(comp: Compiled) -> dict[str, np.ndarray]:
    """Начало рамки каждого звена в сцене при нуле, м."""
    out = {}
    for name, lk in comp.links.items():
        j = lk.joint
        out[name] = (np.asarray(j.origin, float) / 1000.0
                     if j is not None and j.kind == "revolute" else np.zeros(3))
    return out


def _v(values) -> str:
    return " ".join(f"{float(x):.9g}" for x in values)


def urdf_text(comp: Compiled, *, name: str, meshes: dict[str, str],
              colours: dict[str, str] | None = None, payloads: tuple[str, ...] = ()) -> str:
    """URDF: `meshes` — звено → путь к STL (мм) относительно файла URDF."""
    mech = comp.mechanism
    origin = link_origins(comp)
    lines = ['<?xml version="1.0" encoding="utf-8"?>',
             "<!-- Datum: рамка звена — оси сцены в точке на оси его сустава; СИ. "
             "Сетки — только вид (visual): столкновений в файле нет. -->",
             f"<robot name={quoteattr(name)}>"]

    def inertial(mass: float, com, inertia) -> list[str]:
        i = np.asarray(inertia, float)
        return ["    <inertial>",
                f'      <origin xyz="{_v(com)}" rpy="0 0 0"/>',
                f'      <mass value="{mass:.9g}"/>',
                f'      <inertia ixx="{i[0, 0]:.9g}" ixy="{i[0, 1]:.9g}" ixz="{i[0, 2]:.9g}" '
                f'iyy="{i[1, 1]:.9g}" iyz="{i[1, 2]:.9g}" izz="{i[2, 2]:.9g}"/>',
                "    </inertial>"]

    for link, lk in comp.links.items():
        lines.append(f"  <link name={quoteattr(link)}>")
        lines += inertial(lk.mass_kg, np.asarray(lk.com_m, float) - origin[link], lk.inertia_kgm2)
        if link in meshes:
            rgb = "0.6 0.64 0.7"
            if colours and colours.get(link):
                c = colours[link].lstrip("#")
                rgb = _v(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))
            lines += ["    <visual>",
                      f'      <origin xyz="{_v(-origin[link])}" rpy="0 0 0"/>',
                      f"      <geometry><mesh filename={quoteattr(meshes[link])} "
                      f'scale="0.001 0.001 0.001"/></geometry>',
                      f'      <material name={quoteattr("цвет " + link)}>'
                      f'<color rgba="{rgb} 1"/></material>',
                      "    </visual>"]
        lines.append("  </link>")

    for link, lk in comp.links.items():
        j = lk.joint
        if lk.parent is None:
            continue
        shift = origin[link] - origin[lk.parent]
        if j is None:
            lines += [f"  <joint name={quoteattr(link)} type=\"fixed\">",
                      f"    <parent link={quoteattr(lk.parent)}/>",
                      f"    <child link={quoteattr(link)}/>",
                      f'    <origin xyz="{_v(shift)}" rpy="0 0 0"/>', "  </joint>"]
            continue
        si = math.pi / 180.0 if j.kind == "revolute" else 1e-3
        effort = j.drive.limit if j.drive is not None else 0.0
        speed = j.drive.speed * si if j.drive is not None else 0.0
        lines += [f"  <joint name={quoteattr(link)} type=\"{j.kind}\">",
                  f"    <parent link={quoteattr(lk.parent)}/>",
                  f"    <child link={quoteattr(link)}/>",
                  f'    <origin xyz="{_v(shift)}" rpy="0 0 0"/>',
                  f'    <axis xyz="{_v(j.axis)}"/>',
                  f'    <limit lower="{j.limits[0] * si:.9g}" upper="{j.limits[1] * si:.9g}" '
                  f'effort="{effort:.9g}" velocity="{speed:.9g}"/>']
        if j.follows is not None:
            master, ratio = j.follows
            m_si = math.pi / 180.0 if mech.links[master].joint.kind == "revolute" else 1e-3
            lines.append(f"    <mimic joint={quoteattr(master)} "
                         f'multiplier="{ratio * si / m_si:.9g}" offset="0"/>')
        lines.append("  </joint>")

    for pname in payloads:
        p = mech.payloads[pname]
        label = f"груз {pname}"
        at = np.asarray(p.at, float) / 1000.0
        lines.append(f"  <link name={quoteattr(label)}>")
        lines += inertial(p.mass_g / 1000.0, np.zeros(3), np.eye(3) * 1e-9)
        lines += ["  </link>",
                  f"  <joint name={quoteattr(label)} type=\"fixed\">",
                  f"    <parent link={quoteattr(p.link)}/>",
                  f"    <child link={quoteattr(label)}/>",
                  f'    <origin xyz="{_v(at - origin[p.link])}" rpy="0 0 0"/>', "  </joint>"]
    lines.append("</robot>")
    return "\n".join(lines) + "\n"
