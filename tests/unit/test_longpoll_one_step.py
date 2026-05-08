"""One-step longpoll entry point: longpoll(radio_id=...) and the legacy
category-string forms (positional + keyword) all dispatch correctly."""
from __future__ import annotations

import httpx
import pytest
import respx

from audd import AsyncAudD, AudD
from audd._callbacks import derive_longpoll_category

_API_TOKEN = "d29ebb205488e3b414bcc0c50432463e"
_RADIO_ID = 42
_EXPECTED_CATEGORY = derive_longpoll_category(_API_TOKEN, _RADIO_ID)


def _ok_match() -> httpx.Response:
    return httpx.Response(200, json={
        "status": "success",
        "result": {
            "radio_id": _RADIO_ID, "timestamp": "x",
            "results": [{"artist": "A", "title": "T", "score": 99}],
        },
        "timestamp": 1234,
    })


# ---------------------------------------------------------------------------
# Sync Streams.longpoll
# ---------------------------------------------------------------------------


@respx.mock
def test_longpoll_radio_id_derives_category_and_dispatches() -> None:
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return _ok_match()

    respx.get("https://api.audd.io/longpoll/").mock(side_effect=handler)
    client = AudD(api_token=_API_TOKEN)
    with client.streams.longpoll(
        radio_id=_RADIO_ID, skip_callback_check=True, timeout=1,
    ) as poll:
        for m in poll.matches:
            assert m.song.artist == "A"
            break
    assert captured[0].url.params["category"] == _EXPECTED_CATEGORY


@respx.mock
def test_longpoll_category_keyword_dispatches_directly() -> None:
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return _ok_match()

    respx.get("https://api.audd.io/longpoll/").mock(side_effect=handler)
    client = AudD(api_token=_API_TOKEN)
    with client.streams.longpoll(
        category="cat-abc", skip_callback_check=True, timeout=1,
    ) as poll:
        for _m in poll.matches:
            break
    assert captured[0].url.params["category"] == "cat-abc"


@respx.mock
def test_longpoll_positional_category_still_works() -> None:
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return _ok_match()

    respx.get("https://api.audd.io/longpoll/").mock(side_effect=handler)
    client = AudD(api_token=_API_TOKEN)
    with client.streams.longpoll(
        "cat-xyz", skip_callback_check=True, timeout=1,
    ) as poll:
        for _m in poll.matches:
            break
    assert captured[0].url.params["category"] == "cat-xyz"


def test_longpoll_both_category_and_radio_id_raises_typeerror() -> None:
    client = AudD(api_token=_API_TOKEN)
    with pytest.raises(TypeError, match="exactly one"):
        client.streams.longpoll(
            category="cat-abc",
            radio_id=_RADIO_ID,
            skip_callback_check=True,
        )


def test_longpoll_neither_category_nor_radio_id_raises_typeerror() -> None:
    client = AudD(api_token=_API_TOKEN)
    with pytest.raises(TypeError, match="requires one of"):
        client.streams.longpoll(skip_callback_check=True)


# ---------------------------------------------------------------------------
# Async AsyncStreams.longpoll
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_async_longpoll_radio_id_derives_category_and_dispatches() -> None:
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return _ok_match()

    respx.get("https://api.audd.io/longpoll/").mock(side_effect=handler)
    async with AsyncAudD(api_token=_API_TOKEN) as audd:
        poll = await audd.streams.longpoll(
            radio_id=_RADIO_ID, skip_callback_check=True, timeout=1,
        )
        async with poll:
            async for m in poll.matches:
                assert m.song.artist == "A"
                break
    assert captured[0].url.params["category"] == _EXPECTED_CATEGORY


@pytest.mark.asyncio
@respx.mock
async def test_async_longpoll_category_keyword_dispatches_directly() -> None:
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return _ok_match()

    respx.get("https://api.audd.io/longpoll/").mock(side_effect=handler)
    async with AsyncAudD(api_token=_API_TOKEN) as audd:
        poll = await audd.streams.longpoll(
            category="cat-abc", skip_callback_check=True, timeout=1,
        )
        async with poll:
            async for _m in poll.matches:
                break
    assert captured[0].url.params["category"] == "cat-abc"


@pytest.mark.asyncio
@respx.mock
async def test_async_longpoll_positional_category_still_works() -> None:
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return _ok_match()

    respx.get("https://api.audd.io/longpoll/").mock(side_effect=handler)
    async with AsyncAudD(api_token=_API_TOKEN) as audd:
        poll = await audd.streams.longpoll(
            "cat-xyz", skip_callback_check=True, timeout=1,
        )
        async with poll:
            async for _m in poll.matches:
                break
    assert captured[0].url.params["category"] == "cat-xyz"


@pytest.mark.asyncio
async def test_async_longpoll_both_category_and_radio_id_raises_typeerror() -> None:
    async with AsyncAudD(api_token=_API_TOKEN) as audd:
        with pytest.raises(TypeError, match="exactly one"):
            await audd.streams.longpoll(
                category="cat-abc",
                radio_id=_RADIO_ID,
                skip_callback_check=True,
            )


@pytest.mark.asyncio
async def test_async_longpoll_neither_category_nor_radio_id_raises_typeerror() -> None:
    async with AsyncAudD(api_token=_API_TOKEN) as audd:
        with pytest.raises(TypeError, match="requires one of"):
            await audd.streams.longpoll(skip_callback_check=True)
