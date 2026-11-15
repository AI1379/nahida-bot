"""Credential resolution and opaque resource references scoped to account bindings."""

from contextlib import asynccontextmanager
from hashlib import sha256
import os
from typing import Any, AsyncIterator, Literal
from uuid import NAMESPACE_URL, uuid5

from nahida_bot_sdk.api import BotAPI
from .config import CalDAVConfig, CalendarBinding, TaskBinding
from .records import parse
from .transport import CalDAVError, DavClient, Resource


class CalDAVRuntime:
    def __init__(self, api: BotAPI, config: CalDAVConfig) -> None:
        self.api, self.config = api, config

    @asynccontextmanager
    async def connection(
        self, actor: str, role: Literal["tasks", "calendar"]
    ) -> AsyncIterator[tuple[DavClient, TaskBinding | CalendarBinding, str]]:
        account = self.config.account(actor, role)
        binding = getattr(account, role)
        assert binding is not None
        config = self.config.connections[binding.connection]
        password = (
            os.environ.get(config.password_env)
            if config.password_env
            else await self.api.plugin_secret_get(config.password_secret)
        )
        if not password:
            raise CalDAVError("configured_credential_unavailable")
        async with DavClient(config, password) as client:
            yield client, binding, account.timezone

    @staticmethod
    def key(actor: str, record_id: str) -> str:
        if not actor:
            raise PermissionError("An authenticated account is required")
        return f"reference:{sha256(actor.encode()).hexdigest()}:{record_id}"

    async def remember(
        self,
        actor: str,
        connection: str,
        collection: str,
        resource: Resource,
        kind: str,
    ) -> tuple[str, list[Any]]:
        _, items = parse(resource.data, kind)
        uid = str(items[0]["UID"])
        if any(str(item["UID"]) != uid for item in items):
            raise CalDAVError("multiple_uids_in_resource")
        digest = sha256(
            f"{actor}|{connection}|{collection}|{uid}".encode()
        ).hexdigest()[:32]
        record_id = ("ext_" if kind == "VTODO" else "evt_") + digest
        await self.api.plugin_data_set(
            self.key(actor, record_id),
            {
                "connection": connection,
                "collection": collection,
                "href": resource.href,
                "uid": uid,
            },
        )
        return record_id, items

    async def resolve(
        self,
        actor: str,
        record_id: str,
        connection: str,
        collection: str,
        client: DavClient,
        kind: str,
    ) -> Resource:
        reference = await self.api.plugin_data_get(self.key(actor, record_id))
        if (
            not reference
            or reference["connection"] != connection
            or reference["collection"] != collection
        ):
            raise CalDAVError("item_not_in_current_write_binding")
        resource = await client.get(client.within(reference["href"], collection))
        _, items = parse(resource.data, kind)
        if any(str(item["UID"]) != reference["uid"] for item in items):
            raise CalDAVError("resource_identity_changed_refresh")
        return resource

    @staticmethod
    def new_uid(actor: str, connection: str, collection: str, operation: str) -> str:
        return uuid5(
            NAMESPACE_URL, f"nahida:{actor}:{connection}:{collection}:{operation}"
        ).hex
