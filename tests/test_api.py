"""Contract tests using HTTP fixtures; never contact Listonic."""

import asyncio
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.lists_assistant.api import (
    AmbiguousWrite,
    ApiError,
    AuthError,
    ForbiddenError,
    ListonicClient,
    NotFoundError,
    amount_text,
    identifier,
)

BASE = "https://api.listonic.com"
LOGIN = BASE + "/api/loginextended?autoDestruct=1&autoMerge=1&provider=password"
REFRESH = BASE + "/api/loginextended?autoDestruct=1&autoMerge=1&provider=refresh_token"


@pytest.fixture
async def client():
    async with aiohttp.ClientSession() as session:
        yield ListonicClient(session, {"access_token": "old", "refresh_token": "r1"})


async def test_login_and_rotation(client):
    client.on_tokens = AsyncMock()
    with aioresponses() as http:
        http.post(LOGIN, payload={"access_token": "first", "refresh_token": "r2"})
        await client.login(" test@example.com ", "test-only")
        request = next(iter(http.requests.values()))[0].kwargs
        assert request["data"]["username"] == "test@example.com"
        assert request["data"]["password"] == "test-only"
        assert "clientauthorization" in request["headers"]
        http.post(REFRESH, payload={"access_token": "second"})
        await client.refresh()
        assert client.tokens["access_token"] == "second"
        assert client.tokens["refresh_token"] == "r2"
        assert client.on_tokens.await_count == 2
        assert "password" not in client.tokens


async def test_simultaneous_rejection_refreshes_once(client):
    with aioresponses() as http:
        http.post(REFRESH, payload={"access_token": "new", "refresh_token": "r2"})
        await asyncio.gather(*(client.refresh("old") for _ in range(8)))
        assert sum(len(calls) for calls in http.requests.values()) == 1


async def test_request_retries_401_once(client):
    with aioresponses() as http:
        http.get(BASE + "/x", status=401)
        http.post(REFRESH, payload={"access_token": "new"})
        http.get(BASE + "/x", status=401)
        with pytest.raises(AuthError):
            await client.request("GET", "/x")


@pytest.mark.parametrize(
    "status,error", [(403, ForbiddenError), (404, NotFoundError), (400, ApiError)]
)
async def test_status_errors(client, status, error):
    with aioresponses() as http:
        http.get(BASE + "/x", status=status)
        with pytest.raises(error):
            await client.request("GET", "/x")


@pytest.mark.parametrize("status", [500, 504])
async def test_write_not_replayed(client, status):
    with aioresponses() as http:
        http.post(BASE + "/api/lists/1/items", status=status)
        with pytest.raises(AmbiguousWrite):
            await client.add_item("1", "Milk")
        assert sum(len(calls) for calls in http.requests.values()) == 1


async def test_retry_after(client):
    with (
        aioresponses() as http,
        patch(
            "custom_components.lists_assistant.api.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep,
    ):
        http.get(BASE + "/x", status=429, headers={"Retry-After": "2"})
        http.get(BASE + "/x", payload=[])
        assert await client.request("GET", "/x") == []
        sleep.assert_awaited_once_with(2)


async def test_crud_contract_and_empty_responses(client):
    with aioresponses() as http:
        http.post(BASE + "/api/lists", payload={"Id": "1"}, status=201)
        assert (await client.create_list("Home"))["Id"] == "1"
        http.post(BASE + "/api/lists/1/items", payload={"Id": "2"}, status=201)
        await client.add_item("1", "Milk", amount=0.1, unit="l")
        call = list(http.requests.values())[-1][0].kwargs
        assert call["json"] == {"name": "Milk", "amount": "0.1", "unit": "l"}
        http.patch(BASE + "/api/lists/1/items/2", status=200)
        await client.update_item("1", "2", checked=1, description="", amount="2")
        http.delete(BASE + "/api/lists/1/items/2", status=204)
        await client.delete_item("1", "2")
        http.patch(BASE + "/api/lists/1", status=200)
        await client.update_list("1", Active=0)
        call = list(http.requests.values())[-1][0].kwargs
        assert call["json"] == {"Id": "1", "Active": 0}


async def test_full_snapshot_and_missing_items(client):
    with aioresponses() as http:
        http.get(
            BASE + "/api/lists?archive=false&includeItems=true&includeShares=true",
            payload=[{"Id": "1", "Name": "Home"}, {"Id": "2", "Active": 0}],
        )
        http.get(
            BASE + "/api/lists/1/items",
            payload=[{"Id": "3", "Name": "Milk"}, {"Id": "4", "Deleted": 1}],
        )
        lists = await client.lists()
        assert list(lists) == ["1"]
        assert len(lists["1"]["Items"]) == 1


async def test_profile(client):
    with aioresponses() as http:
        http.get(BASE + "/api/account/userinfo", payload={"Username": "account"})
        assert (await client.profile())["Username"] == "account"


@pytest.mark.parametrize(
    "value,expected", [(0.1, "0.1"), ("1,5", "1.5"), (2, "2"), ("", "")]
)
def test_quantity(value, expected):
    assert amount_text(value) == expected


@pytest.mark.parametrize("value", ["NaN", "inf", -1, "milk"])
def test_invalid_quantity(value):
    with pytest.raises(ValueError):
        amount_text(value)


def test_identifier():
    assert identifier(123) == "123"
    with pytest.raises(ApiError):
        identifier("../account")


@pytest.mark.parametrize("status", [400, 401, 403])
async def test_login_rejected(client, status):
    with aioresponses() as http:
        http.post(LOGIN, status=status)
        with pytest.raises(AuthError):
            await client.login("test@example.com", "test-only")


async def test_auth_server_failure_keeps_tokens(client):
    original = dict(client.tokens)
    with aioresponses() as http:
        http.post(REFRESH, status=503)
        with pytest.raises(ApiError):
            await client.refresh()
    assert client.tokens == original


@pytest.mark.parametrize("payload", [{}, [], {"access_token": None}])
async def test_invalid_token_response(client, payload):
    with aioresponses() as http:
        http.post(REFRESH, payload=payload)
        with pytest.raises(ApiError):
            await client.refresh()


async def test_missing_refresh(client):
    client.tokens.pop("refresh_token")
    with pytest.raises(AuthError):
        await client.refresh()


async def test_restore_session_without_access_token(client):
    client.tokens.pop("access_token")
    with aioresponses() as http:
        http.post(REFRESH, payload={"access_token": "restored"})
        http.get(BASE + "/x", payload={})
        assert await client.request("GET", "/x") == {}


@pytest.mark.parametrize("method,error", [("GET", ApiError), ("POST", AmbiguousWrite)])
async def test_network_failure(client, method, error):
    with aioresponses() as http:
        http.add(BASE + "/x", method=method, exception=TimeoutError())
        with pytest.raises(error):
            await client.request(method, "/x")


async def test_auth_timeout(client):
    with aioresponses() as http:
        http.post(REFRESH, exception=TimeoutError())
        with pytest.raises(ApiError):
            await client.refresh()


async def test_read_retries_bounded(client):
    with (
        aioresponses() as http,
        patch(
            "custom_components.lists_assistant.api.asyncio.sleep",
            new_callable=AsyncMock,
        ),
    ):
        http.get(
            BASE + "/x", status=503, headers={"Retry-After": "invalid"}, repeat=True
        )
        with pytest.raises(ApiError):
            await client.request("GET", "/x")
        assert sum(len(calls) for calls in http.requests.values()) == 3


async def test_rate_limited_write(client):
    with aioresponses() as http:
        http.post(BASE + "/x", status=429)
        with pytest.raises(ApiError):
            await client.request("POST", "/x")


async def test_invalid_profile_and_snapshot(client):
    with aioresponses() as http:
        http.get(BASE + "/api/account/userinfo", payload={})
        with pytest.raises(ApiError):
            await client.profile()
        url = BASE + "/api/lists?archive=false&includeItems=true&includeShares=true"
        http.get(url, payload={})
        with pytest.raises(ApiError):
            await client.lists()
        http.get(url, payload=[{"Id": "1"}])
        http.get(BASE + "/api/lists/1/items", payload={})
        with pytest.raises(ApiError):
            await client.lists()


@pytest.mark.parametrize("delay", ["120", "NaN", "inf"])
async def test_long_retry_after_does_not_retry_early(client, delay):
    with aioresponses() as http:
        http.get(BASE + "/x", status=429, headers={"Retry-After": delay})
        with pytest.raises(ApiError):
            await client.request("GET", "/x")
        assert sum(len(calls) for calls in http.requests.values()) == 1


@pytest.mark.parametrize(
    "payload",
    [
        [None],
        [{"Id": "1", "Items": [None]}],
        [{"Id": "1", "Items": [{}]}],
        [{"Id": "1", "Items": [{"Id": "2"}]}],
        [{"Id": "1", "Items": [{"Id": "2", "Name": None}]}],
    ],
)
async def test_malformed_snapshot_never_looks_empty(client, payload):
    with aioresponses() as http:
        http.get(
            BASE + "/api/lists?archive=false&includeItems=true&includeShares=true",
            payload=payload,
        )
        with pytest.raises(ApiError):
            await client.lists()
