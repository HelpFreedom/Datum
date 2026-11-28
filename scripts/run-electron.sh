#!/usr/bin/env bash
# Запуск окна так, как это делает оболочка: через настоящий Electron.
#
# Зачем отдельный скрипт: `npm start` набирают и люди, и проверочные сценарии
# (`make ui`). Прямой вызов `electron` ломается в двух местах:
#   * в терминале внутри Electron-приложения (VS Code, DSH) стоит
#     ELECTRON_RUN_AS_NODE=1, и Electron работает как Node — окна нет;
#   * путь к бинарнику разный: dist/electron на Linux,
#     Electron.app/Contents/MacOS/Electron на macOS. Его знает сам пакет —
#     node_modules/electron/path.txt.
set -euo pipefail
cd "$(dirname "$0")/.."
unset ELECTRON_RUN_AS_NODE
exec "node_modules/electron/dist/$(cat node_modules/electron/path.txt)" "$@"
