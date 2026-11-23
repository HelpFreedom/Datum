"""Общее для всех тестов: общие данные Datum — в стороне от настоящих.

Библиотека покупного и опыт печати лежат вне проекта, в данных самого
человека. Тесты зовут операции по-настоящему (перебор реестра в
`test_hostile_args` — в том числе `print_log` и `component_save` в отдельном
процессе), и без этого писали бы в его базу. Переменная ставится в окружение
процесса: её наследуют и воркеры изолированных операций.
"""

from __future__ import annotations

import os
import tempfile

os.environ["DATUM_DATA_HOME"] = tempfile.mkdtemp(prefix="datum-test-data-")
