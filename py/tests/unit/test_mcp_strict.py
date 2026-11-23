"""MCP не глотает неизвестные аргументы.

Найдено разбором настоящих сессий агента: `part_build(part="крышка")`
(верное имя — `parts`) молча собирал ВСЕ детали и возвращал ответ про весь
проект; `part_delete(dryrun=true)` разбирался бы в настоящее удаление. SDK
лишние ключи отбрасывает до вызова, а проверка REST видела их уже очищенными.
"""

from __future__ import annotations

import pytest

from datum.tools.mcp_adapter import build_server
from datum.tools.registry import OPS, unknown_arguments

from mcp.server.mcpserver.exceptions import ToolError


@pytest.fixture(scope="module")
def server():
    return build_server()


async def _refusal(server, name: str, args: dict) -> str:
    with pytest.raises(ToolError) as caught:
        await server.call_tool(name, args)
    return str(caught.value)


async def test_every_tool_rejects_an_unknown_argument(server, monkeypatch):
    """Перебором реестра: лишний ключ — отказ, и операция НЕ вызвана."""
    called: list[str] = []

    tools = await server.list_tools()
    assert len(tools) == sum(1 for s in OPS.values() if s.mcp)
    for tool in tools:
        inner = server._tool_manager.get_tool(tool.name)

        real = inner.fn

        async def spy(_name=tool.name, _real=real, **kwargs):
            if "__datum_arg_error__" not in kwargs:
                called.append(_name)
                return {"report": "вызвана"}
            return await _real(**kwargs)

        monkeypatch.setattr(inner, "fn", spy)
        error = await _refusal(server, tool.name, {"нет_такого_аргумента": 1})
        assert "нет аргумента «нет_такого_аргумента»" in error, (tool.name, error)
        assert tool.name in error
    assert called == []


async def test_schema_says_no_additional_properties(server):
    for tool in await server.list_tools():
        schema = tool.model_dump(by_alias=True)["inputSchema"]
        assert schema.get("additionalProperties") is False, tool.name


async def test_part_instead_of_parts_is_refused_with_a_hint(server, tmp_path):
    error = await _refusal(server, "part_build", {"project": str(tmp_path), "part": "крышка"})
    assert "возможно, parts" in error and "есть: project, parts, draft" in error


async def test_dryrun_is_not_a_real_delete(server, tmp_path):
    error = await _refusal(server, "part_delete",
                           {"project": str(tmp_path), "part": "x", "dryrun": True})
    assert "возможно, dry_run" in error


async def test_wrong_type_and_missing_are_said_in_russian(server, tmp_path):
    error = await _refusal(server, "section", {"project": str(tmp_path), "part": "x",
                                               "plane": "y", "at": 1})
    assert "plane: недопустимое значение" in error and "pydantic" not in error
    error = await _refusal(server, "part_view", {"project": str(tmp_path)})
    assert "part: обязателен, но не передан" in error


def test_unknown_arguments_helper():
    assert unknown_arguments(["parts", "draft"], {"parts": []}) == ""
    text = unknown_arguments(["z_from", "z_to"], {"z0": 1, "z1": 2})
    assert text.startswith("нет аргументов «z0»") and "есть: z_from, z_to" in text


# -------------------------------------- ответ: компактный текст и сами картинки
async def test_views_come_back_as_pictures_not_as_paths_to_read(server, tmp_path):
    """Каждый вид стоил двух ходов — операция, потом Read картинки; ход при
    длинном контексте — это перечитывание всего контекста."""
    import base64
    import json
    import shutil
    from pathlib import Path

    latch = tmp_path / "latch"
    shutil.copytree(Path(__file__).resolve().parents[3] / "examples" / "latch", latch,
                    ignore=shutil.ignore_patterns(".datum", "output"))
    result = await server.call_tool("part_view", {"project": str(latch), "part": "крышка"})
    kinds = [type(c).__name__ for c in result.content]
    assert kinds == ["TextContent", "ImageContent"]
    reply = json.loads(result.content[0].text)
    assert "\n" not in result.content[0].text, "текст — компактный JSON, без отступов"
    picture = base64.b64decode(result.content[1].data)
    assert picture[:8] == b"\x89PNG\r\n\x1a\n"
    assert picture == (latch / reply["image"]["path"]).read_bytes(), "та же картинка, что по пути"
    assert result.structured_content is None, "ответ один, а не дважды"
    # Без картинки — один текст; отказ — без картинок.
    plain = await server.call_tool("project_info", {"project": str(latch)})
    assert [type(c).__name__ for c in plain.content] == ["TextContent"]


def test_picture_paths_are_found_in_order_and_once():
    from datum.tools.mcp_adapter import _image_paths

    reply = {"image": {"path": "a.png"}, "animation": {"path": "b.gif"},
             "images": {"x": {"path": "c.png"}, "y": {"path": "a.png"}},
             "results": [{"evidence": [{"kind": "png", "path": "d.png"}, {"kind": "json"}]}]}
    assert _image_paths(reply, []) == ["a.png", "c.png", "d.png"]
