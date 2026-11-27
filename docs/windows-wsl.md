# Запуск Datum в Windows через WSL 2

Python, Node.js и Electron запускаются в одном дистрибутиве Linux. Окно
появляется на рабочем столе Windows через WSLg. Отдельный X-сервер или
Docker для этого способа не требуется.

## Подготовка

В PowerShell проверьте версию дистрибутива и наличие WSLg:

```powershell
wsl --list --verbose
wsl --version
```

Выберите дистрибутив с версией **2**. Если WSL ещё не установлен,
см. [установку WSL с графическими приложениями у Microsoft](https://learn.microsoft.com/windows/wsl/tutorials/gui-apps).
Далее команды Linux выполняются из домашнего каталога обычного пользователя.
Клонируйте Datum в файловую систему Linux (например, в `~/Datum`):
для файлов проекта и окружения нужны права и файловые операции Linux.

Пример системных зависимостей для **Debian 12 x86-64** (от администратора Linux):

```sh
apt-get update
apt-get install git ca-certificates make g++ python3.11-venv python3-dev \
  libgtk-3-0 libnss3 libasound2 libdrm2 libgbm1 libxss1 libxtst6 \
  libxkbcommon0 libgl1-mesa-dri libegl1 libglu1-mesa
```

Установите в Linux Node.js 22.12 или новее с npm. Проверьте, что
`node -p process.platform` выводит `linux`, а `python3.11 --version` — Python 3.11.
Выполните [установку Datum](guide.md#установка) в этом дистрибутиве.
Windows и Linux используют разные двоичные модули: каждому окружению нужны
собственные `.venv` и `node_modules`.

## Запуск и проверка

Из каталога репозитория в Linux:

```sh
scripts/datum status
scripts/datum examples/latch
```

Первой командой можно проверить установку бэкенда. Вторая открывает окно
с примером. Тот же запуск из PowerShell (замените имя дистрибутива и путь):

```powershell
wsl -d Debian --cd /home/alex/Datum --exec ./scripts/datum examples/latch
```

Для тестов разработчика выполните `make check` в каталоге репозитория в Linux.
Если окно не появилось, посмотрите `~/.local/state/datum/window.log` в том же
дистрибутиве. Проверьте, что задана переменная `DISPLAY` и существует
`/mnt/wslg`. Запускайте Electron от обычного пользователя.
