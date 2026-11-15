"""Read-only discovery of CalDAV collections; never creates or changes items."""

import argparse
import asyncio
import json
import os

from nahida_bot.plugins.caldav.config import Connection
from nahida_bot.plugins.caldav.transport import DavClient


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password-env", required=True)
    parser.add_argument("--allow-local-http", action="store_true")
    parser.add_argument("--allowed-origin", action="append", default=[])
    options = parser.parse_args()
    password = os.environ.get(options.password_env)
    if not password:
        raise SystemExit("The configured password environment variable is empty")
    connection = Connection(
        url=options.url,
        username=options.username,
        password_env=options.password_env,
        allow_local_http=options.allow_local_http,
        allowed_origins=options.allowed_origin,
    )
    async with DavClient(connection, password) as client:
        print(
            json.dumps(
                {"collections": await client.discover()}, ensure_ascii=False, indent=2
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
