"""Проектные проверки, которых нет в каталоге."""

from datum.checks.result import CheckResult
from datum.doc.project import check


@check("губка.против_зазора", title="Губка глубже зазора", group="analytic")
def lip_deeper_than_gap(P) -> CheckResult:
    """Губка должна перекрывать крышку заметно больше, чем печатный зазор.

    Правило из разбора провала phone-case-gears v3: там было 0.8 − 0.57 = 0.23 мм
    остаточного зацепа, и этого не хватило. Берём двукратный запас.
    """
    ratio = P.LIP_D / P.GAP
    need = 2.0
    ok = ratio >= need
    return CheckResult(
        id="губка.против_зазора", title="Губка глубже зазора", group="analytic",
        status="pass" if ok else "fail",
        message=(f"губка {P.LIP_D:.2f} мм при зазоре {P.GAP:.2f} мм — "
                 f"отношение {ratio:.2f} {'≥' if ok else '<'} {need:.1f}"
                 + ("" if ok else "; остаточный зацеп "
                    f"{P.LIP_D - P.GAP:.2f} мм слишком мал")),
        value=ratio, unit="—", op=">=", threshold=need)
