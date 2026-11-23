"""MCP-сервер Datum — дверь агента.

Инструменты не перечисляются здесь: сервер собирается из реестра операций
(`datum.tools.registry`), того же, из которого строится командная строка.

ВАЖНО: stdio-сервер обязан держать stdout в чистоте — там идёт протокол.
Логи только в stderr.
"""

from __future__ import annotations

from datum.tools.mcp_adapter import INSTRUCTIONS, build_server, main

__all__ = ["build_server", "main", "INSTRUCTIONS", "server"]


def __getattr__(name: str):
    """`server` собирается лениво: иначе импорт модуля тянул бы весь стек."""
    if name == "server":
        return build_server()
    raise AttributeError(name)


if __name__ == "__main__":
    raise SystemExit(main())
