"""Двухзвенная рука на двух сервах — пример симуляции механизма.

Детали построены в СБОРОЧНОМ положении (рука вытянута вдоль +X), печатаются
каждая в своей позе. Механизм `рука` — те же детали плюс покупные сервы и
балласт; массы печатных — по их настройкам печати (`print=`)."""
from datum.build import P, bed_centre, mechanism, part
from datum.geom import box, cyl_y, union
from datum.sim import Mechanism, drive, revolute


def _origin():
    cx, cy = bed_centre()
    return cx, cy


def _shoulder_at():
    cx, cy = _origin()
    return cx, cy, P.TOWER_H


@part("основание", color="#6f7a84", print_up="+Z")
def base():
    """Плита и стойка плеча; балласт — в кармане плиты."""
    cx, cy = _origin()
    w, t = P.BASE_W, P.BASE_T
    plate = box(cx - w / 2, cx + w / 2, cy - w / 2, cy + w / 2, 0, t)
    tower = box(cx - 15, cx + 15, cy - P.LINK_W, cy + P.LINK_W, t, P.TOWER_H - P.LINK_W / 2 - 1)
    return union([plate, tower])


@part("плечо", color="#c9772e", print_up="+Y", print={"walls": 3, "infill": 0.15})
def upper():
    x, y, z = _shoulder_at()
    h = P.LINK_W / 2
    # У оси — скругление, а не угол: прямоугольный хвост за осью при подъёме
    # плеча на 44° опускался в стойку (sim_clearance, 278 мм³).
    return union([box(x, x + P.L1, y - h, y + h, z - h, z + h),
                  cyl_y(P.LINK_W, y - h, y + h, x, z), cyl_y(P.LINK_W, y - h, y + h, x + P.L1, z)])


@part("предплечье", color="#2e6f8e", print_up="+Y", print={"walls": 2, "infill": 0.1})
def fore():
    x, y, z = _shoulder_at()
    h = P.LINK_W / 2
    x0 = x + P.L1
    # предплечье — на соседнем слое по Y, чтобы складываться рядом с плечом
    y0 = y + P.LINK_W / 2 + 1
    return union([box(x0, x0 + P.L2, y0, y0 + P.LINK_W * 0.8, z - h * 0.8, z + h * 0.8),
                  cyl_y(P.LINK_W * 0.8, y0, y0 + P.LINK_W * 0.8, x0, z)])


def _servo(x, y, z):
    """Корпус сервы ST3215 по габариту (45×24×35) — для формы и центра масс."""
    return box(x - 22.5, x + 22.5, y - 12, y + 12, z - 17.5, z + 17.5)


@mechanism("рука")
def arm():
    """Плечо и локоть — сервы ST3215; рука стоит на столе (без струбцины)."""
    x, y, z = _shoulder_at()
    cx, cy = _origin()
    m = Mechanism(base="resting")
    root = m.link("основание")
    root.add(base(), part="основание")
    root.add(_servo(x, y - P.LINK_W - 13, z - 25), mass_g=P.SERVO_G, label="серва плеча")
    root.add(box(cx - 40, cx + 40, cy - 40, cy + 40, 0, 4), mass_g=P.BALLAST_G, label="балласт")
    sh = m.link("плечо", parent="основание",
                joint=revolute("-Y", [x, y, z], [-20, 120], drive=drive(P.SERVO_T, 90)))
    sh.add(upper(), part="плечо")
    sh.add(_servo(x + P.L1, y - P.LINK_W - 1, z), mass_g=P.SERVO_G, label="серва локтя")
    el = m.link("предплечье", parent="плечо",
                joint=revolute("-Y", [x + P.L1, y, z], [-140, 140], drive=drive(P.SERVO_T, 90)))
    el.add(fore(), part="предплечье")
    m.payload("груз", link="предплечье", mass_g=P.LOAD_G, at=[x + P.L1 + P.L2, y + P.LINK_W, z])
    m.scenario("перенос", [{"плечо": 0, "предплечье": 0},
                            {"плечо": 60, "предплечье": -100},
                            {"плечо": 10, "предплечье": -20},
                            {"плечо": 0, "предплечье": 0}], payloads=["груз"])
    return m
