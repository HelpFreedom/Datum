"""Пол носового блока: выходное окно и карман под шибер.

Порт `nozzle.py::_nozzle_floor` из toothpaste-dispenser. Упрощено до сути:
пластина, круглое выходное окно Ø5 и карман, в котором ходит шибер. Шейка
хвостовика проходит пол насквозь — через неё палец каретки ведёт шибер.
"""

from datum.build import P, part
from datum.geom import box, cyl_z, diff


@part("пол", color="#8a94a6", print_up="+Z")
def build():
    top = P.NOZ_Z0 + P.FLOOR_PLATE_T
    plate = box(-P.NOZ_HALF_W, P.NOZ_HALF_W, P.NOZ_Y0, P.NOZ_Y1, P.NOZ_Z0, top)

    # Карман шибера: длина хода плюс сам шибер, с зазором скольжения по кругу.
    pocket = box(P.SLIDE_X0 - 0.3, P.SLIDE_X0 + P.SLIDE_L + P.SLIDE_TRAVEL + 0.3,
                 P.TUBE_Y - P.SLIDE_W / 2 - 0.2, P.TUBE_Y + P.SLIDE_W / 2 + 0.2,
                 P.NOZ_Z0 + P.FLOOR_T, top + 1)

    # Выход: сквозное окно под срез горла тюбика.
    port = cyl_z(P.PORT_D, P.NOZ_Z0 - 1, top + 1, 0, P.TUBE_Y)

    # Щель под шейку хвостовика: она ходит вместе с шибером, поэтому щель
    # длиннее шейки ровно на ход.
    neck = box(13.0 - 0.3, 19.0 + P.SLIDE_TRAVEL + 0.3,
               P.TUBE_Y - P.SLIDE_W / 2 - 0.2, P.TUBE_Y - P.SLIDE_W / 2 + 3.3,
               P.NOZ_Z0 - 1, top + 1)

    return diff(plate, [pocket, port, neck])
