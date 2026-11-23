#!/usr/bin/env bash
# Поддельный claude для сценария crash.cjs: печатает строку и повторяет ввод.
# Настоящий здесь не нужен — проверяется труба окно ↔ оболочка, а не агент.
echo "поддельный claude: до падения окна"
while IFS= read -r line; do echo "эхо: $line"; done
