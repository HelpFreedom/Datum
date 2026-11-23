"""Поиск нечисел (nan, ±inf) во входах — одно правило на всех.

Нечисло, пропущенное внутрь, не роняет расчёт, а даёт ЛОЖНЫЙ ответ: любое
сравнение с nan ложно, и «объём упора ≥ nan» у проверки зацепа выходил «OK»,
сечение в nan — «пустым», луч из nan — «не встретил поверхности». Поэтому
отказ — до работы, с именем того, что не число.

Бесконечность бывает законной там, где она значит «без предела»
(`max_force: .inf` в проверке), поэтому её пропускают по `allow_inf`;
nan законным не бывает нигде.
"""

from __future__ import annotations

import math
from typing import Any

__all__ = ["non_finite"]


def non_finite(value: Any, where: str, *, allow_inf: bool = False) -> tuple[str, str] | None:
    """Первое нечисло в значении: (где, как записано) или None.

    Смотрит вглубь списков, кортежей и словарей (сдвиг, правки профиля,
    состояние окна, аргументы проверки).
    """
    if isinstance(value, float):
        if math.isnan(value) or (math.isinf(value) and not allow_inf):
            return where, repr(value)
        return None
    if isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            found = non_finite(item, f"{where}[{i}]", allow_inf=allow_inf)
            if found is not None:
                return found
    elif isinstance(value, dict):
        for k, item in value.items():
            found = non_finite(item, f"{where}.{k}", allow_inf=allow_inf)
            if found is not None:
                return found
    return None
