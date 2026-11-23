"""checks.yaml правится под блокировкой проекта и по свежему тексту."""

from __future__ import annotations

import threading

from datum.doc import checks_edit


def test_two_edits_at_once_both_land(tmp_path):
    """Прежде каждая правка читала файл, считала и писала сама: из двух
    одновременных в файле оставалась одна."""
    (tmp_path / "checks").mkdir()
    path = tmp_path / "checks" / "checks.yaml"
    path.write_text("- id: а\n  use: single_body\n", encoding="utf-8")
    gate = threading.Barrier(8)

    def add(k: int):
        gate.wait()
        checks_edit.update(tmp_path, lambda text: text + f"- id: п{k}\n  use: single_body\n")

    threads = [threading.Thread(target=add, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert checks_edit.ids_of(path.read_text("utf-8")) == {"а", *(f"п{k}" for k in range(8))}


def test_none_writes_nothing_and_a_missing_file_reads_as_empty(tmp_path):
    seen = []
    assert checks_edit.update(tmp_path, lambda text: seen.append(text)) is None
    assert seen == [""] and not (tmp_path / "checks" / "checks.yaml").exists()
    checks_edit.update(tmp_path, lambda text: "- id: б\n  use: single_body\n")
    assert checks_edit.ids_of((tmp_path / "checks" / "checks.yaml").read_text("utf-8")) == {"б"}
