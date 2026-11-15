"""Transport boundaries and calendar semantics that must fail closed."""

import httpx
import pytest

from nahida_bot.plugins.caldav.config import Connection
from nahida_bot.plugins.caldav.event_reads import availability
from nahida_bot.plugins.caldav.transport import CalDAVError, DavClient, strong_etag
from nahida_bot.plugins.calendar.models import (
    AvailabilityRequest,
    EventCreate,
    TimeWindow,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/",
        "https://user:password@example.com/",
        "https://example.com/?token=secret",
    ],
)
def test_connection_rejects_unsafe_credentials_and_nonlocal_http(url):
    with pytest.raises(ValueError):
        Connection(
            url=url,
            username="test",
            password_env="TEST_PASSWORD",
            allow_local_http=True,
        )


@pytest.mark.parametrize(
    "revision", ["", "*", 'W/"weak"', '"safe"\r\nInjected: yes', "unquoted"]
)
def test_strong_revision_required(revision):
    with pytest.raises(CalDAVError):
        strong_etag(revision)


async def test_credentials_are_not_forwarded_to_unapproved_redirects():
    config = Connection(
        url="https://dav.example.test/", username="test", password_env="TEST_PASSWORD"
    )
    seen = []

    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(
            302, headers={"Location": "https://attacker.example.test/"}
        )

    async with DavClient(config, "synthetic") as client:
        await client.http.aclose()
        client.http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), auth=("test", "synthetic")
        )
        with pytest.raises(CalDAVError, match="cross_origin"):
            await client.request("PROPFIND", config.url)
    assert seen == ["dav.example.test"]


def test_icloud_shard_discovery_is_narrowly_scoped():
    client = DavClient(
        Connection(
            url="https://caldav.icloud.com/",
            username="test",
            password_env="TEST_PASSWORD",
        ),
        "test",
    )
    assert (
        client.url("https://p123-caldav.icloud.com/principal/")
        == "https://p123-caldav.icloud.com/principal/"
    )
    for url in (
        "https://p123-caldav.icloud.com.attacker.test/",
        "https://mail.icloud.com/",
        "http://p123-caldav.icloud.com/",
    ):
        with pytest.raises(CalDAVError):
            client.url(url)


async def test_missing_calendar_data_is_not_reported_as_free_time():
    config = Connection(
        url="https://dav.example.test/", username="test", password_env="TEST_PASSWORD"
    )
    xml = '<D:multistatus xmlns:D="DAV:"><D:response><D:href>/calendar/private.ics</D:href><D:status>HTTP/1.1 403 Forbidden</D:status></D:response></D:multistatus>'
    async with DavClient(config, "test") as client:
        await client.http.aclose()
        client.http = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(207, text=xml))
        )
        with pytest.raises(CalDAVError, match="incomplete_calendar_query"):
            await client.query("https://dav.example.test/calendar/", "VEVENT")


def test_xml_entity_expansion_is_rejected():
    response = httpx.Response(
        207, content=b'<!DOCTYPE x [<!ENTITY x "expanded">]><x>&x;</x>'
    )
    with pytest.raises(CalDAVError, match="invalid_xml"):
        DavClient.tree(response)


def test_invalid_and_unbounded_time_windows_are_rejected():
    with pytest.raises(ValueError):
        TimeWindow(start="2026-11-06T09:00:00", end="2026-11-06T10:00:00")
    with pytest.raises(ValueError):
        TimeWindow(start="2026-01-01T00:00:00Z", end="2027-01-01T00:00:00Z")
    with pytest.raises(ValueError):
        EventCreate(
            title="bad",
            start="2026-11-06",
            end="2026-11-06T10:00:00Z",
            operation_id="op",
        )


def test_availability_merges_overlaps_and_ignores_instant_events():
    window = AvailabilityRequest(
        start="2026-11-06T09:00:00+08:00",
        end="2026-11-06T13:00:00+08:00",
        duration_minutes=30,
    )
    events = [
        {
            "start": "2026-11-06T09:00:00+08:00",
            "end": "2026-11-06T10:30:00+08:00",
            "busy": True,
            "status": "CONFIRMED",
        },
        {
            "start": "2026-11-06T10:00:00+08:00",
            "end": "2026-11-06T11:00:00+08:00",
            "busy": True,
            "status": "CONFIRMED",
        },
        {
            "start": "2026-11-06T12:00:00+08:00",
            "end": "2026-11-06T12:00:00+08:00",
            "busy": True,
            "status": "CONFIRMED",
        },
    ]
    result = availability(events, window, "Asia/Shanghai")
    assert result["free_windows"] == [
        {"start": "2026-11-06T11:00:00+08:00", "end": "2026-11-06T13:00:00+08:00"}
    ]
