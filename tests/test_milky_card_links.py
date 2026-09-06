"""Tests for extracting web links from QQ share-card segments."""

from __future__ import annotations

import json

from nahida_bot.channels.milky.card_links import (
    extract_light_app_info,
    extract_xml_card_info,
)
from nahida_bot.channels.milky.segments import (
    parse_incoming_segments,
    render_segments_plain_text,
)

BILIBILI_CARD = {
    "app": "com.tencent.structmsg",
    "desc": "",
    "view": "news",
    "ver": "0.0.0.1",
    "prompt": "[分享]【原神】4.0 枫丹MV",
    "meta": {
        "news": {
            "action": "",
            "android_pkg_name": "tv.danmaku.bili",
            "app_type": 1,
            "appid": 100951776,
            "cover": "https://i0.hdslb.com/bfs/archive/cover.jpg",
            "desc": "up主: 某某 | 播放量 123",
            "jump_url": "https://b23.tv/BV1xx411c7mD",
            "preview": "https://i0.hdslb.com/bfs/archive/cover.jpg",
            "tag": "哔哩哔哩",
            "title": "【原神】4.0 枫丹MV",
        }
    },
}

XIAOHONGSHU_CARD = {
    "app": "com.tencent.miniapp_01",
    "desc": "",
    "view": "notification",
    "ver": "0.0.0.1",
    "prompt": "[分享]纳西妲角色解析",
    "meta": {
        "miniapp": {
            "appid": "1100346044",
            "desc": "关于纳西妲的一切",
            "jump_url": "https://www.xiaohongshu.com/discovery/item/64f0a1b2000000001e03f5c7",
            "page_path": "pages/note/detail/index",
            "preview": "http://sns-img-qc.xhscdn.com/abc.jpg",
            "title": "纳西妲角色解析",
        }
    },
}

XML_CARD = (
    '<msg serviceID="1" templateID="1" action="web" brief="[分享]【原神】枫丹MV" '
    'source="哔哩哔哩" url="https://b23.tv/BV1xx411c7mD">'
    '<item layout="2"><title>【原神】枫丹MV</title>'
    "<summary>up主: 某某</summary></item></msg>"
)


def test_bilibili_structmsg_card_extracts_jump_url_and_title() -> None:
    info = extract_light_app_info(json.dumps(BILIBILI_CARD))

    assert info.urls == ("https://b23.tv/BV1xx411c7mD",)
    assert info.title == "【原神】4.0 枫丹MV"


def test_xiaohongshu_miniapp_card_extracts_jump_url_and_title() -> None:
    info = extract_light_app_info(json.dumps(XIAOHONGSHU_CARD))

    assert info.urls == (
        "https://www.xiaohongshu.com/discovery/item/64f0a1b2000000001e03f5c7",
    )
    assert info.title == "纳西妲角色解析"


def test_cover_and_preview_images_are_not_treated_as_links() -> None:
    info = extract_light_app_info(json.dumps(BILIBILI_CARD))

    assert all("hdslb.com" not in url for url in info.urls)


def test_jump_url_ranked_before_generic_url_fields() -> None:
    payload = json.dumps(
        {
            "app": "com.tencent.structmsg",
            "meta": {
                "news": {
                    "url": "https://example.com/generic",
                    "jump_url": "https://example.com/jump",
                }
            },
        }
    )

    info = extract_light_app_info(payload)

    assert info.urls[0] == "https://example.com/jump"
    assert info.urls[1] == "https://example.com/generic"


def test_html_escaped_and_slash_escaped_urls_are_normalized() -> None:
    payload = json.dumps(
        {
            "app": "com.tencent.structmsg",
            "meta": {
                "news": {
                    "jump_url": "https://example.com/a?x=1&amp;y=2",
                }
            },
        }
    )

    info = extract_light_app_info(payload)

    assert info.urls == ("https://example.com/a?x=1&y=2",)


def test_prompt_used_as_title_when_meta_has_no_title() -> None:
    payload = json.dumps(
        {
            "app": "com.tencent.structmsg",
            "prompt": "[分享]只有提示词的分享",
            "meta": {"news": {"jump_url": "https://example.com/prompt-only"}},
        }
    )

    info = extract_light_app_info(payload)

    assert info.urls == ("https://example.com/prompt-only",)
    assert info.title == "只有提示词的分享"


def test_double_encoded_json_payload_still_extracts() -> None:
    payload = json.dumps(json.dumps(XIAOHONGSHU_CARD))

    info = extract_light_app_info(payload)

    assert info.urls == (
        "https://www.xiaohongshu.com/discovery/item/64f0a1b2000000001e03f5c7",
    )


def test_invalid_json_falls_back_to_regex_scan() -> None:
    payload = '{"app": broken json with link https://example.com/regex-fallback }'

    info = extract_light_app_info(payload)

    assert info.urls == ("https://example.com/regex-fallback",)


def test_escaped_slash_urls_survive_regex_fallback() -> None:
    payload = 'garbage \\"jump\\":\\"https:\\/\\/example.com\\/escaped\\"'

    info = extract_light_app_info(payload)

    assert info.urls == ("https://example.com/escaped",)


def test_non_http_schemes_are_ignored() -> None:
    payload = json.dumps(
        {"meta": {"miniapp": {"jump_url": "mqqapi://scheme/only", "title": "t"}}}
    )

    info = extract_light_app_info(payload)

    assert info.urls == ()


def test_xml_card_extracts_root_url_and_title() -> None:
    info = extract_xml_card_info(XML_CARD)

    assert info.urls == ("https://b23.tv/BV1xx411c7mD",)
    assert info.title == "【原神】枫丹MV"


def test_xml_card_reads_url_from_nested_elements() -> None:
    payload = (
        '<msg serviceID="2" brief="[分享]歌曲">'
        '<item layout="2"><title>歌曲名</title>'
        '<audio url="https://music.example.com/song/1"/>'
        "</item></msg>"
    )

    info = extract_xml_card_info(payload)

    assert info.urls == ("https://music.example.com/song/1",)
    # brief wins over nested <title> text when both are present.
    assert info.title == "歌曲"


def test_xml_card_with_dtd_skips_et_parsing_and_regex_scans_raw() -> None:
    payload = (
        '<!DOCTYPE msg [<!ENTITY e "https://example.com/entity">]>'
        '<msg serviceID="1" url="&e;"><item><title>t</title></item></msg>'
    )

    info = extract_xml_card_info(payload)

    # ET parsing is refused (entity expansion hardening); the regex fallback
    # still surfaces the literal URL from the payload text.
    assert info.urls == ("https://example.com/entity",)


def test_empty_and_malformed_payloads_yield_empty_info() -> None:
    assert extract_light_app_info("") == extract_light_app_info("not json at all")
    assert extract_xml_card_info("") == extract_xml_card_info("<broken")


def test_light_app_segment_renders_with_url_in_plain_text() -> None:
    segments = parse_incoming_segments(
        [
            {
                "type": "text",
                "data": {"text": "看看这个 "},
            },
            {
                "type": "light_app",
                "data": {
                    "app_name": "com.tencent.structmsg",
                    "json_payload": json.dumps(BILIBILI_CARD),
                },
            },
        ]
    )
    rendered = render_segments_plain_text(segments)

    assert rendered.startswith("看看这个 ")
    assert "app_name=com.tencent.structmsg" in rendered
    assert "title=【原神】4.0 枫丹MV" in rendered
    assert "url=https://b23.tv/BV1xx411c7mD" in rendered


def test_xml_segment_renders_with_url_in_plain_text() -> None:
    segments = parse_incoming_segments(
        [{"type": "xml", "data": {"service_id": 1, "xml_payload": XML_CARD}}]
    )
    rendered = render_segments_plain_text(segments)

    assert "service_id=1" in rendered
    assert "title=【原神】枫丹MV" in rendered
    assert "url=https://b23.tv/BV1xx411c7mD" in rendered


def test_unresolvable_card_keeps_placeholder_rendering() -> None:
    segments = parse_incoming_segments(
        [
            {
                "type": "light_app",
                "data": {"app_name": "com.tencent.unknown", "json_payload": "{}"},
            },
            {"type": "xml", "data": {"service_id": 0, "xml_payload": "<msg/>"}},
        ]
    )
    rendered = render_segments_plain_text(segments)

    assert "[LightApp: app_name=com.tencent.unknown]" in rendered
    assert "[XML: service_id=0]" in rendered


def test_pure_miniapp_card_does_not_surface_preview_image_as_link() -> None:
    """纯小程序卡（无网页版 jump_url）不应把封面图当成分享链接。"""
    payload = json.dumps(
        {
            "app": "com.tencent.miniapp_01",
            "view": "notification",
            "prompt": "[分享]肯德基会员小程序",
            "meta": {
                "miniapp": {
                    "appid": "1100346044",
                    "title": "肯德基会员",
                    "desc": "点餐小程序",
                    "page_path": "pages/index/index",
                    "preview": "https://thirdwx.qlogo.cn/preview.jpg",
                }
            },
        }
    )

    info = extract_light_app_info(payload)

    assert info.urls == ()
    assert info.title == "肯德基会员"


def test_unranked_non_asset_url_field_still_extracted() -> None:
    payload = json.dumps(
        {
            "app": "com.tencent.structmsg",
            "meta": {
                "news": {
                    "title": "t",
                    "source_link": "https://example.com/source",
                }
            },
        }
    )

    info = extract_light_app_info(payload)

    assert info.urls == ("https://example.com/source",)


def test_render_pure_miniapp_card_shows_title_without_url() -> None:
    segments = parse_incoming_segments(
        [
            {
                "type": "light_app",
                "data": {
                    "app_name": "com.tencent.miniapp_01",
                    "json_payload": json.dumps(
                        {
                            "app": "com.tencent.miniapp_01",
                            "prompt": "[分享]肯德基会员小程序",
                            "meta": {
                                "miniapp": {
                                    "appid": "1100346044",
                                    "title": "肯德基会员",
                                    "page_path": "pages/index/index",
                                    "preview": "https://thirdwx.qlogo.cn/preview.jpg",
                                }
                            },
                        }
                    ),
                },
            }
        ]
    )

    rendered = render_segments_plain_text(segments)

    assert "title=肯德基会员" in rendered
    assert "url=" not in rendered
    assert "qlogo.cn" not in rendered


def test_xml_picture_cover_url_not_surfaced() -> None:
    payload = (
        '<msg serviceID="1" brief="[分享]相册">'
        '<item layout="2"><picture url="https://example.com/cover.jpg"/></item>'
        "</msg>"
    )

    info = extract_xml_card_info(payload)

    assert info.urls == ()
    assert info.title == "相册"
