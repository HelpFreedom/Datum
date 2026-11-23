"""Правка checks.yaml куском текста: комментарии остаются, соседи не задеты."""

from __future__ import annotations

import pytest
import yaml

from datum.doc.checks_edit import ChecksEditError, append_entries, remove_entries

TEXT = """\
# шапка: как устроен файл

- id: а
  use: single_body
  args: {part: "@а"}

# б держит крышку — рассуждение в NOTES.md
- id: б
  use: min_wall
  args:
    part: "@б"
    wall: 1.2

- id: в
  use: bbox_fits
  args: {part: "@в"}
"""


@pytest.mark.parametrize("drop", [{0}, {1}, {2}, {0, 2}, {0, 1, 2}])
def test_removing_and_returning_gives_the_same_list(drop):
    data = yaml.safe_load(TEXT)
    out, removed = remove_entries(TEXT, drop)
    assert (yaml.safe_load(out) or []) == [d for k, d in enumerate(data) if k not in drop]
    assert out.startswith("# шапка")                        # шапка не уходит никогда
    back = yaml.safe_load(append_entries(out, removed))
    assert sorted(back, key=lambda d: d["id"]) == data


def test_the_comment_goes_with_its_entry():
    out, removed = remove_entries(TEXT, {1})
    assert "рассуждение" not in out and "рассуждение" in removed[0]
    assert "id: а" in out and "id: в" in out


def test_a_flow_style_file_is_refused():
    with pytest.raises(ChecksEditError):
        remove_entries("[{id: а, use: single_body}]\n", {0})
