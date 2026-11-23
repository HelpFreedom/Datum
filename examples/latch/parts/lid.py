"""Крышка, ложащаяся в карман под губку."""

from datum.build import P, part
from datum.geom import box


@part("крышка", color="#c9723b", print_up="+Z")
def build():
    """Пластина толщиной LID_T под самой губкой, с печатным зазором GAP по кругу."""
    return box(P.WALL + P.GAP, P.CASE_W - P.WALL - P.GAP,
               P.WALL + P.GAP, P.CASE_D - P.WALL - P.GAP,
               P.LID_Z - P.LID_T, P.LID_Z)
