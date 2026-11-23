"""CLI Datum.

Команды не перечисляются здесь: они строятся из реестра операций
(`datum.tools.registry`), того же, из которого собираются инструменты MCP. Так
человек и агент гарантированно имеют одинаковые возможности — списки не ведутся
руками и потому не расходятся.
"""

from __future__ import annotations

import sys

from datum.tools.cli_adapter import build_parser, main

__all__ = ["main", "build_parser"]


if __name__ == "__main__":
    sys.exit(main())
