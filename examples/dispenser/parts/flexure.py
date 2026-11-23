"""Флексура: параллелограмм из двух листов.

Порт `cradle.py::_flexure`. Два листа держат подвижный блок так, что он ходит
вниз-вверх, не поворачиваясь. Печатается НА БОКУ, поэтому слои идут вдоль
листа, а не поперёк — иначе лист расслоится на первом же нажатии.

Форма листа под прогибом — кубическая: у направляемой балки оба конца
сохраняют угол, и середина уходит вдвое дальше, чем у консоли. Ровно от этого
зависит деформация, которую считает проверка.
"""

from datum.build import P, part
from datum.geom import box, prism_yz, union


@part("флексура", color="#7fb08a", print_up="+X")
def build(sag: float = 0.0):
    """sag — опускание подвижного блока от напечатанного положения, мм."""
    half = P.LEAF_B / 2
    anchor_y = (-80.0, -65.0)
    mover_y = (5.0, 33.0)
    leaf_z = (27.5, 43.5)

    anchor = box(-half, half, anchor_y[0], anchor_y[1], P.BASE_T - 3.0, leaf_z[1] + P.FLEX_PRELOAD + 2.5)
    mover = box(-half, half, mover_y[0], mover_y[1],
                25.5 - sag, 45.5 - sag)

    parts = [anchor, mover]
    for z_free in leaf_z:
        parts.append(prism_yz(_leaf_profile(z_free, sag, anchor_y[1], mover_y[0],
                                            float(P.LEAF_T)),
                              -half, half))
    return union(parts)


def _leaf_profile(z_free: float, sag: float, y0: float, y1: float,
                  thickness: float, steps: int = 36):
    """Сечение листа в плоскости YZ под прогибом.

    Изогнутая ось: z(u) = z_free − sag·(3u² − 2u³). Это форма направляемой
    балки — с нулевым углом на обоих концах. Взять вместо неё дугу или прямую
    значило бы получить другую деформацию и другой ответ проверки.
    """
    top, bottom = [], []
    for i in range(steps + 1):
        u = i / steps
        y = y0 + (y1 - y0) * u
        centre = z_free - sag * (3 * u * u - 2 * u ** 3)
        # Утолщение у корней: там наибольший момент, и печатный лист трескается
        # именно на стыке с телом.
        near = min(y - y0, y1 - y)
        t = thickness + 2.0 * max(0.0, 1 - near / 3.0) ** 2
        top.append((y, centre + t / 2))
        bottom.append((y, centre - t / 2))
    return ([(y0 - 0.5, top[0][1])] + top + [(y1 + 0.5, top[-1][1]),
            (y1 + 0.5, bottom[-1][1])] + bottom[::-1] + [(y0 - 0.5, bottom[0][1])])
