"""Плита под ящик: гнёзда под каждую его ячейку."""

from datum.build import P, part
from datum.std.gridfinity import baseplate


@part("плита", color="#8a93a5", print_up="+Z")
def build():
    return baseplate(int(P.GRID_X), int(P.GRID_Y))
