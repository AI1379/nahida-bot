"""Batch-assign ``sender_account_key`` on existing cron jobs.

Usage::

    python scripts/migrate_cron_sender_account.py --db data/nahida.db \\
        --account-key "milky:user:123456" --dry-run

    python scripts/migrate_cron_sender_account.py --db data/nahida.db \\
        --account-key "milky:user:123456" --apply
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Batch-assign sender_account_key on existing cron jobs",
    )
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument(
        "--account-key",
        required=True,
        help='Target sender_account_key, e.g. "milky:user:123456"',
    )
    parser.add_argument(
        "--platform",
        help="Only update jobs for this platform (e.g. 'milky', 'telegram')",
    )
    parser.add_argument(
        "--chat-id",
        help="Only update jobs for this chat_id",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Show what would change without applying (default)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually apply the update",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        parser.error(f"Database not found: {db_path}")

    account_key: str = args.account_key.strip()
    if not account_key:
        parser.error("--account-key must not be empty")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        column_exists = conn.execute(
            "SELECT 1 FROM pragma_table_info('cron_jobs') WHERE name = 'sender_account_key'"
        ).fetchone()
        if column_exists is None:
            parser.exit(
                status=1,
                message="Error: sender_account_key column not found. "
                "Run the bot once first to apply Migration 022.\n",
            )

        where = "WHERE 1=1"
        params: list[str] = []
        if args.platform:
            where += " AND platform = ?"
            params.append(args.platform)
        if args.chat_id:
            where += " AND chat_id = ?"
            params.append(args.chat_id)

        # Show matching jobs
        rows = conn.execute(
            f"SELECT job_id, platform, chat_id, created_by_user_id, "
            f"sender_account_key, prompt "
            f"FROM cron_jobs {where} ORDER BY created_at",
            params,
        ).fetchall()

        if not rows:
            print("No matching cron jobs found.")
            return

        print(f"Found {len(rows)} cron job(s):\n")
        for row in rows:
            current: str = row["sender_account_key"]
            print(
                f"  {row['job_id']}  platform={row['platform']}  "
                f"chat_id={row['chat_id']}  "
                f"created_by={row['created_by_user_id'] or '(none)'}  "
                f"account_key={current or '(empty)'}"
            )
            print(
                f"    prompt: {row['prompt'][:80]}{'...' if len(row['prompt'] or '') > 80 else ''}"
            )

        if args.apply:
            cursor = conn.execute(
                f"UPDATE cron_jobs SET sender_account_key = ? {where}",
                [account_key] + params,
            )
            conn.commit()
            print(
                f"\nUpdated {cursor.rowcount} cron job(s) to sender_account_key={account_key!r}."
            )
        else:
            print(
                f"\nDry run — would update {len(rows)} cron job(s) to "
                f"sender_account_key={account_key!r}."
            )
            print("Re-run with --apply to commit.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
