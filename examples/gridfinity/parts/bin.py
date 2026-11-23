"""Ящик Gridfinity с отсеками."""

from datum.build import P, part
from datum.std.gridfinity import Bin


@part("ящик", color="#7f9cc0", print_up="+Z")
def build():
    ящик = Bin(int(P.GRID_X), int(P.GRID_Y), int(P.UNITS), wall=P.WALL)
    return ящик.compartments(int(P.COLS), int(P.ROWS)).solid()
