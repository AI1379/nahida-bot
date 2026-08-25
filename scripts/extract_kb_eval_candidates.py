"""Extract KB-eval candidate queries from a production snapshot.

A curation aid for the gold eval set (``scripts/kb_eval_queries.json``,
kb-direction.md §4): scans user turns for question-like messages that mention
Teyvat entity names, dedupes by prefix, and prints TSV candidates for manual
curation. Opens the snapshot read-only (URI mode) — never a write path.

Usage:
    uv run python scripts/extract_kb_eval_candidates.py --db data/nahida-server-20260822.db \
        [--max 80] [--min-chars 4] [--max-chars 80]
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

# Names overlapping the Teyvat wiki corpus (characters + major entities).
# Kept as a flat list on purpose: this is a recall-oriented curation filter,
# not a knowledge source; misses only mean fewer candidates.
ENTITY_NAMES = [
    "纳西妲", "草神", "钟离", "芙宁娜", "七七", "雷电将军", "雷神", "提纳里",
    "赛诺", "艾尔海森", "妮露", "流浪者", "散兵", "温迪", "枫原万叶", "万叶",
    "神里绫华", "绫华", "甘雨", "胡桃", "宵宫", "珊瑚宫心海", "心海", "五郎",
    "荒泷一斗", "一斗", "夜兰", "白术", "卡维", "莱依拉", "珐露珊", "迪希雅",
    "凯瑟琳", "阿贝多", "可莉", "迪卢克", "凯亚", "安柏", "丽莎", "莫娜",
    "北斗", "凝光", "香菱", "重云", "行秋", "刻晴", "辛焱", "优菈", "申鹤",
    "魈", "八重神子", "神子", "九条裟罗", "托马", "鹿野院平藏", "平藏",
    "久岐忍", "柯莱", "多莉", "坎蒂丝", "琳妮特", "林尼", "菲米尼",
    "那维莱特", "莱欧斯利", "夏沃蕾", "嘉明", "千织", "阿蕾奇诺", "仆人",
    "克洛琳德", "希格雯", "希诺宁", "基尼奇", "卡齐娜", "玛拉妮", "茜特菈莉",
    "瓦雷莎", "伊安珊", "世界树", "净善宫", "须弥", "枫丹", "璃月", "稻妻",
    "蒙德", "纳塔", "至冬", "坎瑞亚", "深渊", "愚人众", "执行官", "天理",
    "七神", "魔神", "尘歌壶", "元素爆发", "命之座", "圣遗物",
]

_QUESTION_RE = re.compile(
    r"[?？]|是什么|哪个|哪些|怎么|如何|为什么|谁|多少|有没有|讲讲|说说|介绍|科普|来点|推荐"
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to the archive snapshot")
    parser.add_argument("--max", type=int, default=80, help="Max candidates printed")
    parser.add_argument("--min-chars", type=int, default=4)
    parser.add_argument("--max-chars", type=int, default=80)
    args = parser.parse_args()

    archive = Path(args.db).resolve()
    if not archive.is_file():
        sys.exit(f"database not found: {archive}")

    name_re = re.compile("|".join(ENTITY_NAMES))
    con = sqlite3.connect(f"file:{archive.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT session_id, content, created_at FROM memory_turns "
            "WHERE role='user' AND length(content) BETWEEN ? AND ?",
            (args.min_chars, args.max_chars),
        ).fetchall()
    finally:
        con.close()

    seen: set[str] = set()
    hits: list[tuple[str, str, str]] = []
    for row in rows:
        content = row["content"].strip()
        if content.startswith(("/", "[Reply")):
            continue
        if name_re.search(content) and _QUESTION_RE.search(content):
            key = content[:30]
            if key in seen:
                continue
            seen.add(key)
            hits.append((row["created_at"], row["session_id"], content))

    print(f"[extract] scanned {len(rows)} user turns, "
          f"{len(hits)} unique question-like candidates")
    print("date\tsession\tcontent")
    for created, session, content in hits[: args.max]:
        print(f"{created[:10]}\t{session}\t{content}")


if __name__ == "__main__":
    main()
