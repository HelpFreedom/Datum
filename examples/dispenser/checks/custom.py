"""Проектные проверки дозатора, которых нет в каталоге."""

from datum.checks.result import CheckResult
from datum.doc.project import check


@check("шибер.перекрытие", title="Шибер перекрывает канал", group="motion")
def shutter_blocks_the_port(P, parts) -> CheckResult:
    """В покое шибер обязан закрывать выход, а на открытии — освобождать.

    Проверяется НЕ положением окна, а объёмом: сколько канала Ø5 занято телом
    шибера. Положение обмануло бы — окно может встать напротив, а телом шибер
    всё равно перекроет край. В оригинале это считалось так же, через
    пересечение с цилиндром канала.
    """
    from datum.geom import cyl_z
    from datum.geom.measure import inter_vol, vol

    z0 = P.NOZ_Z0 + P.FLOOR_T
    top = P.NOZ_Z0 + P.FLOOR_PLATE_T
    channel = cyl_z(P.PORT_D, z0, top, 0, P.TUBE_Y)
    whole = vol(channel)

    closed = inter_vol(parts("шибер", u=0.0), channel) / whole
    opened = inter_vol(parts("шибер", u=P.SHUTTER_OPEN_STROKE), channel) / whole

    ok = closed >= 0.60 and opened <= 0.05
    return CheckResult(
        id="шибер.перекрытие", title="Шибер перекрывает канал", group="motion",
        status="pass" if ok else "fail",
        message=(f"в покое перекрыто {closed * 100:.0f} % канала (нужно ≥ 60), "
                 f"на открытии {opened * 100:.0f} % (нужно ≤ 5)"
                 + ("" if ok else "; паста пойдёт мимо или не пойдёт вовсе")),
        value=closed, unit="", op=">=", threshold=0.60,
        inputs=("шибер",))
