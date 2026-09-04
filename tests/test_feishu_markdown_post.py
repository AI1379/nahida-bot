"""Tests for the Feishu markdown → post converter."""

from __future__ import annotations

import json

from nahida_bot.channels.feishu.markdown_post import (
    looks_like_markdown,
    markdown_to_paragraphs,
    split_markdown,
)


def _flat(paragraphs: list[list[dict]]) -> str:
    pieces: list[str] = []
    for paragraph in paragraphs:
        for element in paragraph:
            pieces.append(str(element.get("text") or ""))
    return "".join(pieces)


def _flat_one(paragraph: list[dict]) -> str:
    return "".join(str(element.get("text") or "") for element in paragraph)


def test_plain_text_single_run() -> None:
    paragraphs = markdown_to_paragraphs("只是普通聊天文本")

    assert paragraphs == [[{"tag": "text", "text": "只是普通聊天文本"}]]
    assert looks_like_markdown("只是普通聊天文本") is False


def test_bold_italic_strike_and_code_styles() -> None:
    paragraphs = markdown_to_paragraphs("**粗** *斜* ~~删~~ `码`")

    runs = paragraphs[0]
    styles = {run["text"]: run.get("style") for run in runs if run["tag"] == "text"}
    assert styles["粗"] == ["bold"]
    assert styles["斜"] == ["italic"]
    assert styles["删"] == ["strikethrough"]
    assert styles["码"] == ["code"]


def test_link_element() -> None:
    paragraphs = markdown_to_paragraphs("见 [文档](https://example.com) 说明")

    links = [element for element in paragraphs[0] if element["tag"] == "a"]
    assert links == [{"tag": "a", "text": "文档", "href": "https://example.com"}]
    assert _flat(paragraphs).startswith("见 ")


def test_heading_becomes_bold_paragraph() -> None:
    paragraphs = markdown_to_paragraphs("## 标题文字")

    assert paragraphs[0][0] == {"tag": "text", "text": "标题文字", "style": ["bold"]}


def test_fenced_code_block() -> None:
    md = "前置\n```python\nprint(1)\nprint(2)\n```\n后置"
    paragraphs = markdown_to_paragraphs(md)

    code_blocks = [p for p in paragraphs if p[0].get("tag") == "code_block"]
    assert len(code_blocks) == 1
    assert code_blocks[0][0]["language"] == "PYTHON"
    assert code_blocks[0][0]["text"] == "print(1)\nprint(2)"
    assert _flat(paragraphs) == "前置print(1)\nprint(2)后置"


def test_unclosed_fence_runs_to_end() -> None:
    paragraphs = markdown_to_paragraphs("```\nabc")

    assert paragraphs[0][0]["tag"] == "code_block"
    assert paragraphs[0][0]["text"] == "abc"


def test_lists_become_bullet_paragraphs() -> None:
    paragraphs = markdown_to_paragraphs("- 一\n- 二\n1. 三")

    assert _flat_one(paragraphs[0]) == "• 一"
    assert _flat_one(paragraphs[1]) == "• 二"
    assert _flat_one(paragraphs[2]) == "1. 三"


def test_blockquote_prefix_and_hr() -> None:
    paragraphs = markdown_to_paragraphs("> 引用行\n\n---")

    assert _flat_one(paragraphs[0]) == "│ 引用行"
    assert paragraphs[1] == [{"tag": "hr"}]


def test_inline_at_tag_becomes_at_element() -> None:
    paragraphs = markdown_to_paragraphs('叫一下 <at user_id="ou_abc123">张三</at> 谢谢')

    at_elements = [element for element in paragraphs[0] if element["tag"] == "at"]
    assert at_elements == [{"tag": "at", "user_id": "ou_abc123", "user_name": "张三"}]


def test_table_rows_kept_as_text() -> None:
    md = "| a | b |\n|---|---|\n| 1 | 2 |"
    paragraphs = markdown_to_paragraphs(md)

    assert paragraphs[0][0]["tag"] == "text"
    assert "a" in paragraphs[0][0]["text"]
    assert looks_like_markdown(md) is False  # tables alone don't trigger post


def test_split_markdown_prefers_paragraph_boundaries() -> None:
    md = "第一段\n\n第二段\n\n第三段"
    chunks = split_markdown(md, limit=10)

    assert all(len(chunk) <= 10 for chunk in chunks)
    assert chunks[0].startswith("第一段")
    assert "第三段" in chunks[-1]


def test_split_markdown_hard_splits_large_paragraph() -> None:
    md = "长" * 50
    chunks = split_markdown(md, limit=20)

    assert len(chunks) == 3
    assert all(len(chunk) <= 20 for chunk in chunks)


def test_split_markdown_short_text_single_chunk() -> None:
    assert split_markdown("短文本", limit=100) == ["短文本"]
    assert split_markdown("", limit=100) == []


def test_paragraph_json_shape_roundtrip() -> None:
    from nahida_bot.channels.feishu.markdown_post import markdown_to_post_content

    content = json.loads(markdown_to_post_content("**粗**"))
    assert set(content) == {"post"}
    assert set(content["post"]) == {"zh_cn"}
    assert content["post"]["zh_cn"]["title"] == ""
    assert content["post"]["zh_cn"]["content"][0][0] == {
        "tag": "text",
        "text": "粗",
        "style": ["bold"],
    }
