"""Правка checks/checks.yaml ТЕКСТОМ: убрать описания и вернуть их.

Удалённая деталь не должна оставлять за собой проверок, которые ссылаются на
неё и навсегда краснеют ошибкой «нет детали». Перезаписать файл через YAML
значило бы потерять комментарии, а в них у проектов живёт рассуждение, — так
же, как с параметрами (`params_edit.py`). Поэтому описание вырезается куском
текста, от своего «- » до следующего, и возвращается тем же куском.

Правка делается только там, где разбор однозначен: каждое описание начинается
«- » с первой колонки, и каждый кусок сам по себе разбирается в ровно то
описание, что стоит в списке. Иначе — отказ, файл не трогается: пусть лучше
проверка покраснеет, чем тихо пропадёт чужая.
"""

from __future__ import annotations

import yaml

__all__ = ["update", "ChecksEditError", "remove_entries", "append_entries", "ids_of"]


class ChecksEditError(ValueError):
    """Файл устроен не так, чтобы его можно было править куском текста."""


def _load(text: str) -> list:
    data = yaml.safe_load(text) if text.strip() else []
    if data is None:
        data = []
    if not isinstance(data, list):
        raise ChecksEditError("checks/checks.yaml — не список описаний")
    return data


def _starts(lines: list[str]) -> list[int]:
    return [i for i, line in enumerate(lines)
            if line.startswith("- ") or line.rstrip("\r\n") == "-"]


def ids_of(text: str) -> set[str]:
    """id описаний в файле — так же, как их называет проект (id или use)."""
    return {str(d.get("id") or d.get("use")) for d in _load(text) if isinstance(d, dict)}


def remove_entries(text: str, drop: set[int]) -> tuple[str, list[str]]:
    """Вырезать описания с номерами `drop` (с нуля). Вернуть текст и вырезанное."""
    data = _load(text)
    lines = text.splitlines(keepends=True)
    starts = _starts(lines)
    if len(starts) != len(data):
        raise ChecksEditError(
            f"в checks.yaml {len(data)} описаний, а с «- » в первой колонке "
            f"начинается {len(starts)} — править куском текста нельзя")
    # Кусок описания: от его «- » (вместе с комментарием прямо над ним) до
    # начала следующего. Комментарий над первым описанием — шапка файла, его
    # кусок не берёт.
    begins = []
    for k, start in enumerate(starts):
        b = start
        if k > 0:
            while b - 1 > starts[k - 1] and lines[b - 1].lstrip().startswith("#"):
                b -= 1
        begins.append(b)
    ends = begins[1:] + [len(lines)]
    for k in range(len(data)):
        piece = "".join(lines[begins[k]:ends[k]])
        if _load(piece) != [data[k]]:
            raise ChecksEditError(f"описание №{k + 1} не отделяется от соседей куском текста")

    removed, keep = [], []
    for k in range(len(data)):
        piece = lines[begins[k]:ends[k]]
        if k in drop:
            removed.append("".join(piece).rstrip() + "\n")
        else:
            keep.append(piece)
    head = lines[:begins[0]] if begins else lines
    out = "".join(head) + "".join("".join(p) for p in keep)
    out = out.rstrip() + "\n" if out.strip() else ""
    if _load(out) != [d for k, d in enumerate(data) if k not in drop]:  # pragma: no cover
        raise ChecksEditError("после правки checks.yaml разбирается не так, как ожидалось")
    return out, removed


def append_entries(text: str, pieces: list[str]) -> str:
    """Вернуть вырезанные описания в конец файла."""
    before = _load(text)
    added = [d for piece in pieces for d in _load(piece)]
    out = (text.rstrip() + "\n\n" if text.strip() else "") + "\n".join(
        piece.rstrip() + "\n" for piece in pieces)
    if _load(out) != before + added:
        raise ChecksEditError("вернуть описания в checks.yaml куском текста не вышло")
    return out


def update(root, change):
    """Прочитать `checks/checks.yaml`, изменить `change(текст) → новый текст |
    None` и записать — под ИСКЛЮЧИТЕЛЬНОЙ блокировкой проекта, как положения
    (`placement.update`). None — не писать.

    Прежде удаление и возврат детали читали файл, считали правку и писали его
    каждый сам: две одновременные правки (окно удаляет одну деталь, агент —
    другую) обе отвечали «готово», а проверки первой возвращались в файл со
    ссылкой на уже удалённую деталь (аудит 2026-11-21, §6.3.9). Файл читается
    ЗАНОВО под блокировкой, а не из открытого проекта."""
    from pathlib import Path

    from datum.doc.file_lock import exclusive_lock
    from datum.doc.params_edit import write_atomically

    root = Path(root)
    path = root / "checks" / "checks.yaml"
    lock_path = root / ".datum" / "checks.lock"
    with exclusive_lock(lock_path):
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        new = change(text)
        if new is not None and new != text:
            path.parent.mkdir(exist_ok=True)
            write_atomically(path, new)          # недописанный — хуже прежнего
        return new
