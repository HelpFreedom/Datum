"""Где лежат ОБЩИЕ данные Datum — те, что не принадлежат одному проекту:
библиотека покупного (`components/`) и опыт печати (`lessons.json`).

Обычно это `$XDG_DATA_HOME/datum` (рядом с проектами «Без названия»).
`DATUM_DATA_HOME` переносит только эти данные — так их кладут на общий диск,
и так тесты держат их в стороне: перебор операций из реестра
(`test_hostile_args`) зовёт `print_log` и `component_save` по-настоящему, и
без этого писал бы в базу самого человека. Переменная `XDG_DATA_HOME` для
тестов не годится: на неё смотрят и чужие программы (контейнер слайсера).
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["data_home"]


def data_home(env=None) -> Path:
    env = os.environ if env is None else env
    own = env.get("DATUM_DATA_HOME")
    if own:
        return Path(own)
    base = env.get("XDG_DATA_HOME") or str(Path(env.get("HOME", "~")).expanduser()
                                           / ".local" / "share")
    return Path(base) / "datum"
