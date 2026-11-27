"""Установленный мешер должен загрузиться и участвовать в проверках сетки."""

from importlib.util import find_spec

import pytest

from datum.fem.tetmesh import mesher_available


@pytest.mark.skipif(find_spec("pytetwild") is None, reason="пакет pytetwild не установлен")
def test_installed_mesher_loads():
    assert mesher_available()
