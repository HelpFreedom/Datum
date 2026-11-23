#!/usr/bin/env bash
# Установка зависимостей окна.
#
# Скрипты установки запрещены политикой (.npmrc), поэтому двум пакетам, которым
# они действительно нужны, сборка запускается ЯВНО и поимённо. Это и есть тот
# белый список; с npm 12 он же записан в package.json (`allowScripts`).
set -euo pipefail
cd "$(dirname "$0")/.."

echo "→ зависимости (без скриптов установки)"
npm ci --ignore-scripts

echo "→ сборка тех двоих, кому скрипты нужны"
# npm 12 блокирует скрипты и в `rebuild`, если пакета нет в белом списке
# `allowScripts` (package.json, версии закреплены), а `ignore-scripts=true`
# из .npmrc запрещает их вовсе — поэтому здесь он снят явно. Пакеты вне
# списка по-прежнему ничего не исполняют.
npm rebuild --ignore-scripts=false esbuild node-pty

# Пересборки под ABI Electron НЕ требуется. node-pty 1.1.0 построен на
# node-addon-api, то есть на N-API: двоичный интерфейс стабилен, и один и тот
# же модуль работает и в Node, и в Electron. Проверено: собранный под Node 20
# (ABI 115) он загрузился в Electron 44 (ABI 149) и запустил claude в pty.
# Если будущая версия уйдёт с N-API — есть `npm run rebuild`.

echo "→ Electron: бинарник"
# С Electron 44 пакет не скачивает бинарник сам (скрипта установки у него
# нет — есть `install-electron`). Качает его install.js пакета через
# @electron/get и сверяет по checksums.json; уже скачанный не трогает.
node node_modules/electron/install.js

echo "→ проверка"
node -e "require('./node_modules/esbuild/lib/main.js'); console.log('  esbuild: ок')"
node -e "require('./node_modules/node-pty'); console.log('  node-pty: ок')"
test -x node_modules/electron/dist/electron || { echo "  electron: НЕТ бинарника" >&2; exit 1; }
echo "  electron: ок"
echo "готово. Сборка окна: npm run build"
