"""Unit tests for the blocking Google Keep client wrapper."""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.lists_assistant.keep_api import (
    GoogleKeepClient,
    GoogleKeepError,
    async_exchange_master_token,
)


class FakeListItem:
    def __init__(self, item_id, text, checked=False, parent=None):
        self.id = item_id
        self.text = text
        self.checked = checked
        self.parent = parent

    def delete(self):
        self.parent.items.remove(self)


class FakeList:
    def __init__(self, note_id, title, items=None):
        self.id = note_id
        self.title = title
        self.items = items or []
        self.deleted = False
        self.trashed = False
        self.archived = False

    def add(self, text, checked=False):
        item = FakeListItem(f"item-{len(self.items) + 1}", text, checked, self)
        self.items.append(item)
        return item


class FakeKeep:
    fail = None

    def __init__(self):
        self.list = FakeList("list-id", "Shopping", [FakeListItem("item-id", "Milk")])
        self.deleted_list = FakeList("deleted-id", "Deleted")
        self.deleted_list.deleted = True
        self.other = SimpleNamespace(id="note-id", title="Note")
        self.auth_args = None
        self.sync_count = 0

    def authenticate(self, *args):
        self._raise_if("authenticate")
        self.auth_args = args

    def dump(self):
        return {"cached": True}

    def all(self):
        self._raise_if("all")
        return [self.list, self.deleted_list, self.other]

    def sync(self):
        self._raise_if("sync")
        self.sync_count += 1

    def get(self, list_id):
        return self.list if list_id == self.list.id else None

    @classmethod
    def _raise_if(cls, operation):
        if cls.fail == operation:
            raise RuntimeError(f"test {operation} failure")


@pytest.fixture
def fake_gkeepapi(monkeypatch):
    FakeKeep.fail = None
    module = ModuleType("gkeepapi")
    module.Keep = FakeKeep
    module.node = SimpleNamespace(List=FakeList)
    monkeypatch.setitem(sys.modules, "gkeepapi", module)
    return FakeKeep


async def test_keep_client_auth_lists_refresh_and_mutations(hass, fake_gkeepapi):
    client = GoogleKeepClient(hass, "keep@example.test", "aas_et/test")

    state = await client.async_authenticate()
    assert state == {"cached": True}
    assert client.keep.auth_args == (
        "keep@example.test",
        "aas_et/test",
        None,
    )
    assert await client.async_lists() == [{"id": "list-id", "title": "Shopping"}]
    assert await client.async_refresh() == {"cached": True}
    assert await client.async_items("list-id") == {
        "item-id": {"name": "Milk", "checked": False}
    }

    item_id = await client.async_apply(
        "list-id", "add", None, {"name": "Bread", "checked": True}
    )
    assert item_id == "item-2"
    await client.async_apply(
        "list-id", "update", item_id, {"name": "Bread loaf", "checked": False}
    )
    updated = await client.async_items("list-id")
    assert updated[item_id] == {
        "name": "Bread loaf",
        "checked": False,
    }
    await client.async_apply("list-id", "delete", item_id, None)
    assert item_id not in await client.async_items("list-id")
    assert client.keep.sync_count == 4


async def test_exchange_master_token_keeps_cookie_out_of_result(hass, monkeypatch):
    exchange = Mock(return_value={"Token": "aas_et/test-master-token"})
    module = ModuleType("gpsoauth")
    module.exchange_token = exchange
    monkeypatch.setitem(sys.modules, "gpsoauth", module)

    token = await async_exchange_master_token(
        hass, "keep@example.test", "oauth-cookie-secret", "0123456789abcdef"
    )

    assert token == "aas_et/test-master-token"
    exchange.assert_called_once_with(
        "keep@example.test", "oauth-cookie-secret", "0123456789abcdef"
    )


async def test_exchange_master_token_rejects_missing_token(hass, monkeypatch):
    module = ModuleType("gpsoauth")
    module.exchange_token = Mock(return_value={"Error": "secret response"})
    monkeypatch.setitem(sys.modules, "gpsoauth", module)

    with pytest.raises(GoogleKeepError, match="token exchange failed") as error:
        await async_exchange_master_token(
            hass, "keep@example.test", "oauth-cookie-secret", "0123456789abcdef"
        )

    assert "secret response" not in str(error.value)


@pytest.mark.parametrize(
    ("failure", "method", "message"),
    [
        ("authenticate", "async_authenticate", "authentication failed"),
        ("all", "async_lists", "Could not read"),
        ("sync", "async_refresh", "Could not refresh"),
    ],
)
async def test_keep_client_wraps_auth_read_and_refresh_errors(
    hass, fake_gkeepapi, failure, method, message
):
    fake_gkeepapi.fail = failure
    client = GoogleKeepClient(hass, "keep@example.test", "aas_et/test")

    with pytest.raises(GoogleKeepError, match=message):
        await getattr(client, method)()


async def test_keep_client_rejects_missing_list_and_wraps_write_failure(
    hass, fake_gkeepapi
):
    client = GoogleKeepClient(hass, "keep@example.test", "aas_et/test")
    with pytest.raises(GoogleKeepError, match="unavailable"):
        await client.async_items("missing")
    with pytest.raises(GoogleKeepError, match="unavailable"):
        await client.async_apply(
            "missing", "add", None, {"name": "Milk", "checked": False}
        )

    fake_gkeepapi.fail = "sync"
    with pytest.raises(GoogleKeepError, match="write failed"):
        await client.async_apply(
            "list-id", "add", None, {"name": "Milk", "checked": False}
        )
