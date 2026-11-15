"""Bounded async CalDAV transport with conditional writes and scoped redirects."""

from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

from defusedxml.ElementTree import fromstring
import httpx

from .config import Connection

D = "{DAV:}"
C = "{urn:ietf:params:xml:ns:caldav}"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_RESOURCES = 1000


class CalDAVError(ValueError):
    """Safe user-visible error without URLs, credentials or upstream response bodies."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"CalDAV: {code}")


@dataclass(frozen=True)
class Resource:
    href: str
    etag: str
    data: bytes


def origin(url: str) -> tuple[str, str | None, int]:
    parts = urlsplit(url)
    return (
        parts.scheme,
        parts.hostname,
        parts.port or (443 if parts.scheme == "https" else 80),
    )


class DavClient:
    def __init__(self, config: Connection, password: str) -> None:
        self.config = config
        self.http = httpx.AsyncClient(
            auth=(config.username, password),
            timeout=config.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )

    async def __aenter__(self) -> "DavClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.http.aclose()

    def url(self, value: str, *, base: str = "") -> str:
        resolved = urljoin(base or self.config.url, value)
        parts = urlsplit(resolved)
        if parts.username or parts.password or parts.query or parts.fragment:
            raise CalDAVError("unsafe_resource_url")
        allowed = {
            origin(self.config.url),
            *(origin(url) for url in self.config.allowed_origins),
        }
        apple_shard = (
            urlsplit(self.config.url).hostname == "caldav.icloud.com"
            and parts.scheme == "https"
            and (parts.port in (None, 443))
            and bool(re.fullmatch(r"p\d+-caldav\.icloud\.com", parts.hostname or ""))
        )
        if origin(resolved) not in allowed and not apple_shard:
            raise CalDAVError("cross_origin_resource_rejected")
        if any(piece in {".", ".."} for piece in unquote(parts.path).split("/")):
            raise CalDAVError("unsafe_resource_path")
        return str(httpx.URL(resolved))

    def within(self, value: str, collection: str) -> str:
        resolved = self.url(value, base=collection)
        a, b = urlsplit(resolved), urlsplit(collection)
        if origin(resolved) != origin(collection) or not a.path.startswith(
            b.path.rstrip("/") + "/"
        ):
            raise CalDAVError("resource_outside_collection")
        return resolved

    async def request(
        self,
        method: str,
        url: str,
        *,
        data: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        target = self.url(url)
        try:
            for _ in range(5):
                async with self.http.stream(
                    method, target, content=data, headers=headers
                ) as response:
                    if response.status_code in (301, 302, 307, 308):
                        redirected = self.url(
                            response.headers.get("location", ""), base=target
                        )
                        if method in {"PUT", "DELETE"} and redirected != target:
                            raise CalDAVError("write_redirect_requires_rediscovery")
                        target = redirected
                        continue
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_RESPONSE_BYTES:
                            raise CalDAVError("response_too_large")
                    decoded_headers = dict(response.headers)
                    decoded_headers.pop("content-encoding", None)
                    decoded_headers.pop("content-length", None)
                    return httpx.Response(
                        response.status_code,
                        headers=decoded_headers,
                        content=bytes(content),
                        request=response.request,
                    )
        except httpx.RequestError:
            code = (
                "write_outcome_unknown_query_before_retry"
                if method in {"PUT", "DELETE"}
                else "connection_unavailable"
            )
            raise CalDAVError(code) from None
        raise CalDAVError("redirect_limit")

    @staticmethod
    def require(response: httpx.Response, *codes: int) -> None:
        if response.status_code in codes:
            return
        if response.status_code == 412:
            raise CalDAVError("revision_conflict_refresh_before_retry")
        if response.status_code in (401, 403):
            raise CalDAVError("authentication_or_access_denied")
        if response.status_code == 404:
            raise CalDAVError("resource_not_found")
        raise CalDAVError(f"upstream_http_{response.status_code}")

    @staticmethod
    def tree(response: httpx.Response) -> Any:
        try:
            return fromstring(response.content)
        except Exception:
            raise CalDAVError("invalid_xml_response") from None

    async def propfind(
        self, path: str, properties: str, *, depth: str = "0"
    ) -> httpx.Response:
        body = f'<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav"><D:prop>{properties}</D:prop></D:propfind>'
        response = await self.request(
            "PROPFIND",
            path,
            data=body.encode(),
            headers={"Depth": depth, "Content-Type": "application/xml"},
        )
        self.require(response, 207)
        return response

    async def discover(self) -> list[dict[str, Any]]:
        response = await self.propfind(self.config.url, "<D:current-user-principal/>")
        tree = self.tree(response)
        principal = tree.findtext(f".//{D}current-user-principal/{D}href")
        if not principal:
            raise CalDAVError("principal_not_found")
        principal_url = self.url(principal, base=str(response.url))
        response = await self.propfind(principal_url, "<C:calendar-home-set/>")
        home = self.tree(response).findtext(f".//{C}calendar-home-set/{D}href")
        if not home:
            raise CalDAVError("calendar_home_not_found")
        response = await self.propfind(
            self.url(home, base=str(response.url)),
            "<D:displayname/><C:supported-calendar-component-set/>",
            depth="1",
        )
        result = []
        for row in self.tree(response).findall(f"{D}response"):
            prop = successful_properties(row)
            components = [item.get("name") for item in prop.findall(f".//{C}comp")]
            if components:
                result.append(
                    {
                        "url": self.url(
                            row.findtext(f"{D}href", ""), base=str(response.url)
                        ),
                        "name": prop.findtext(f"{D}displayname", ""),
                        "components": components,
                    }
                )
        return result

    async def collection(self, value: str, kind: str) -> str:
        if not value:
            raise CalDAVError("collection_not_configured")
        response = await self.propfind(
            self.url(value), "<C:supported-calendar-component-set/>"
        )
        kinds = [
            item.get("name") for item in self.tree(response).findall(f".//{C}comp")
        ]
        if kind not in kinds:
            raise CalDAVError(f"collection_does_not_support_{kind}")
        return str(response.url).rstrip("/") + "/"

    async def query(
        self, collection: str, kind: str, *, time_range: tuple[str, str] | None = None
    ) -> list[Resource]:
        window = (
            f'<C:time-range start="{time_range[0]}" end="{time_range[1]}"/>'
            if time_range
            else ""
        )
        body = f'<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav"><D:prop><D:getetag/><C:calendar-data/></D:prop><C:filter><C:comp-filter name="VCALENDAR"><C:comp-filter name="{kind}">{window}</C:comp-filter></C:comp-filter></C:filter></C:calendar-query>'
        response = await self.request(
            "REPORT",
            collection,
            data=body.encode(),
            headers={"Depth": "1", "Content-Type": "application/xml"},
        )
        self.require(response, 207)
        resources = []
        for row in self.tree(response).findall(f"{D}response"):
            prop = successful_properties(row)
            content = prop.findtext(f"{C}calendar-data")
            if content is None:
                raise CalDAVError("incomplete_calendar_query_do_not_assume_empty")
            if content is not None:
                resources.append(
                    Resource(
                        self.within(row.findtext(f"{D}href", ""), collection),
                        prop.findtext(f"{D}getetag", ""),
                        content.encode(),
                    )
                )
        if len(resources) > MAX_RESOURCES:
            raise CalDAVError("too_many_items_narrow_the_query")
        return resources

    async def get(self, href: str) -> Resource:
        response = await self.request("GET", href)
        self.require(response, 200)
        return Resource(
            str(response.url), response.headers.get("etag", ""), response.content
        )

    async def put(self, href: str, data: bytes, *, etag: str | None = None) -> Resource:
        headers = {"Content-Type": "text/calendar; charset=utf-8"}
        if etag is not None:
            headers["If-Match"] = strong_etag(etag)
        else:
            headers["If-None-Match"] = "*"
        response = await self.request("PUT", href, data=data, headers=headers)
        self.require(response, 200, 201, 204)
        return await self.get(href)

    async def delete(self, href: str, etag: str) -> None:
        response = await self.request(
            "DELETE", href, headers={"If-Match": strong_etag(etag)}
        )
        self.require(response, 200, 204)


def strong_etag(value: str) -> str:
    if (
        not value
        or not re.fullmatch(r'"[^"\r\n]*"', value)
        or value.startswith("W/")
        or len(value) > 512
        or "\r" in value
        or "\n" in value
    ):
        raise CalDAVError("strong_revision_required")
    return value


def successful_properties(row: Any) -> Any:
    from xml.etree.ElementTree import Element

    merged = Element("properties")
    for status in row.findall(f"{D}propstat"):
        if " 200 " in status.findtext(f"{D}status", ""):
            prop = status.find(f"{D}prop")
            if prop is not None:
                merged.extend(list(prop))
    return merged
