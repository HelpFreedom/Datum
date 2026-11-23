# Как предложить правку · Contributing

**Русский** · [English](#english)

Спасибо, что хотите помочь. Несколько правил, на которых держится проект.

1. **Перед пулл-реквестом — `make check`.** Это тесты бэкенда и окна, бюджеты
   времени и самопроверка МКЭ. Правка окна — ещё и `make ui` (нужен дисплей).
2. **Язык.** Комментарии, сообщения и тексты для человека — по-русски. Всё,
   что пересекает границу байтов, — ASCII: имена в коде (Python, TS, CSS),
   заголовки HTTP, `b"..."`-литералы, имена файлов в бандле окна.
3. **Числа на экране и в проверках обязаны совпадать.** Окно не пересчитывает
   то, что считает сервер.
4. **Охрану не ослабляем.** Не добавляйте `Access-Control-Allow-*` в сервер и
   настройки, единственное действие которых — снять защиту. Подробности —
   [docs/security.md](docs/security.md).
5. **Проверка — с замером.** Новая проверка или порог — с тестом, который
   ловит и провал, и прохождение; отказ — словами, по-русски, а не `nan` и не
   «зелёное по пустоте».
6. **Зависимости зафиксированы** в `py/requirements.lock` с хешами. Новая
   зависимость — с причиной в `py/pyproject.toml` и обновлённым замком.

Об ошибке — issue с шагами, при которых она повторяется, версией ОС и, если
можно, проектом (`Файл → Сохранить как…` даёт один файл `.datum`).

---

## English

Thank you for wanting to help. A few rules the project relies on:

1. **Run `make check` before a pull request.** It runs backend and window
   tests, time budgets and the FEM self-check. For window changes also run
   `make ui` (needs a display).
2. **Language.** Comments, messages and human-facing text are in Russian.
   Everything that crosses a byte boundary is ASCII: identifiers (Python, TS,
   CSS), HTTP headers, `b"..."` literals, file names in the window bundle.
   Issues and pull requests in English are welcome.
3. **Numbers on screen and in checks must match.** The window never
   recomputes what the server computes.
4. **Do not weaken protection.** No `Access-Control-Allow-*` in the server and
   no settings whose only effect is to remove a safeguard. See
   [docs/security.md](docs/security.md).
5. **A check comes with a measurement.** A new check or threshold needs a test
   that catches both a failure and a pass; refusals are explained in words, not
   `nan` and not "green because nothing was checked".
6. **Dependencies are pinned** in `py/requirements.lock` with hashes. A new
   dependency needs a reason in `py/pyproject.toml` and an updated lock file.

For a bug report, open an issue with steps to reproduce, your OS version and,
if possible, the project (`File → Save as…` produces a single `.datum` file).
