"""Exercise account actions through real HA services and fresh API snapshots."""

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lists_assistant.api import AmbiguousWrite, ApiError, AuthError
from custom_components.lists_assistant.const import CONF_LISTS, DOMAIN
from custom_components.lists_assistant.services import SERVICES


@pytest.fixture
async def account(hass):
    cloud = {
        "1": {"Id": "1", "Name": "Same name", "Items": []},
        "3": {
            "Id": "3",
            "Name": "Same name",
            "Items": [
                {
                    "Id": "8",
                    "Name": "milk",
                    "Checked": 0,
                    "Amount": "2",
                    "Unit": "l",
                    "Price": 3.49,
                    "Description": "preserve",
                }
            ],
        },
    }
    client = MagicMock()
    client.lists = AsyncMock(side_effect=lambda: deepcopy(cloud))

    async def create(name):
        cloud["4"] = {"Id": "4", "Name": name, "Items": []}
        return {"Id": "4"}

    async def change_list(list_id, **fields):
        if fields.get("Active") == 0:
            cloud.pop(list_id)
        else:
            cloud[list_id].update(fields)

    async def add(list_id, **fields):
        cloud[list_id]["Items"].append(
            {
                "Id": "9",
                "Checked": 0,
                **{key.capitalize(): value for key, value in fields.items()},
            }
        )
        return {"Id": "9"}

    async def change(list_id, item_id, **fields):
        item = next(item for item in cloud[list_id]["Items"] if item["Id"] == item_id)
        item.update({key.capitalize(): value for key, value in fields.items()})

    async def delete(list_id, item_id):
        cloud[list_id]["Items"] = [
            item for item in cloud[list_id]["Items"] if item["Id"] != item_id
        ]

    client.create_list = AsyncMock(side_effect=create)
    client.update_list = AsyncMock(side_effect=change_list)
    client.add_item = AsyncMock(side_effect=add)
    client.update_item = AsyncMock(side_effect=change)
    client.delete_item = AsyncMock(side_effect=delete)
    with patch(
        "custom_components.lists_assistant.api.ListonicClient", return_value=client
    ):
        entry = MockConfigEntry(
            domain=DOMAIN, unique_id="account-a", data={"tokens": {}, CONF_LISTS: ["1"]}
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry, client, cloud


async def call(hass, account, action, response=False, **data):
    return await hass.services.async_call(
        DOMAIN,
        action,
        {"config_entry_id": account.entry_id, **data},
        blocking=True,
        return_response=response,
    )


async def test_list_crud_and_unselected_product_metadata(hass, account):
    entry, client, cloud = account
    result = await call(hass, entry, "get_lists", response=True)
    assert {item["Id"] for item in result["lists"]} == {"1", "3"}
    result = await call(hass, entry, "create_list", name="New", response=True)
    assert result == {"list_id": "4"}
    assert "4" not in hass.data[DOMAIN][entry.entry_id].data
    await call(hass, entry, "rename_list", list_id=4, name="Renamed")
    assert cloud["4"]["Name"] == "Renamed"
    result = await call(
        hass,
        entry,
        "add_item",
        list_id="4",
        name="milk",
        amount="2,50",
        unit="l",
        price="3.49",
        description="Details",
        response=True,
    )
    assert result == {"item_id": "9"}
    client.add_item.assert_awaited_once_with(
        "4", name="milk", amount="2.50", unit="l", price=3.49, description="Details"
    )
    await call(hass, entry, "update_item", list_id="3", item_id="8", amount="", price=0)
    client.update_item.assert_awaited_once_with("3", "8", amount="", price=0.0)
    assert cloud["3"]["Items"][0]["Description"] == "preserve"
    await call(
        hass,
        entry,
        "update_item",
        list_id="4",
        item_id="9",
        checked=True,
        name="changed",
    )
    assert cloud["4"]["Items"][0]["Checked"] == 1
    await call(hass, entry, "delete_item", list_id="4", item_id="9")
    assert not cloud["4"]["Items"]
    await call(hass, entry, "delete_list", list_id="4")
    assert "4" not in cloud
    client.update_list.assert_awaited_with("4", Active=0)


@pytest.mark.parametrize(
    "data",
    [
        {"list_id": "../1", "item_id": "8", "price": 3},
        {"list_id": "3", "item_id": "milk", "price": 3},
        {"list_id": "3", "item_id": "8", "price": float("nan")},
        {"list_id": "3", "item_id": "8", "price": -1},
        {"list_id": "3", "item_id": "8", "price": True},
        {"list_id": "3", "item_id": "8", "amount": "nan"},
        {"list_id": "3", "item_id": "8", "amount": "-1"},
        {"list_id": "3", "item_id": "8", "name": "  "},
        {"list_id": "3", "item_id": "8", "checked": "yes"},
        {"list_id": "3", "item_id": "8", "unexpected": "field"},
    ],
)
async def test_invalid_input_never_reaches_api(hass, account, data):
    entry, client, _ = account
    client.lists.reset_mock()
    with pytest.raises(vol.Invalid):
        await call(hass, entry, "update_item", **data)
    client.lists.assert_not_awaited()
    client.update_item.assert_not_awaited()


@pytest.mark.parametrize(
    "data",
    [
        {"list_id": "3", "item_id": "8"},
        {"list_id": "2", "item_id": "8", "name": "edit"},
        {"list_id": "1", "item_id": "8", "name": "edit"},
    ],
)
async def test_missing_target_and_empty_edit_do_not_write(hass, account, data):
    entry, client, _ = account
    with pytest.raises(ServiceValidationError):
        await call(hass, entry, "update_item", **data)
    client.update_item.assert_not_awaited()


@pytest.mark.parametrize(
    "failure", [ApiError("offline"), AuthError("expired"), AmbiguousWrite("unknown")]
)
async def test_write_errors_do_not_retry(hass, account, failure):
    entry, client, _ = account
    client.add_item.side_effect = failure
    with pytest.raises(HomeAssistantError):
        await call(hass, entry, "add_item", list_id="1", name="milk", response=True)
    client.add_item.assert_awaited_once()
    assert not hass.data[DOMAIN][entry.entry_id].last_update_success


async def test_confirmed_creation_survives_failed_readback(hass, account):
    entry, client, _ = account
    client.lists.side_effect = ApiError("offline")
    assert await call(hass, entry, "create_list", name="New", response=True) == {
        "list_id": "4"
    }
    client.create_list.assert_awaited_once()
    assert not hass.data[DOMAIN][entry.entry_id].last_update_success


@pytest.mark.parametrize("result", [None, {}, {"Id": "unsafe"}])
async def test_malformed_create_result_is_uncertain_not_replayed(hass, account, result):
    entry, client, _ = account
    client.create_list.side_effect = None
    client.create_list.return_value = result
    with pytest.raises(HomeAssistantError, match="result is unknown"):
        await call(hass, entry, "create_list", name="New", response=True)
    client.create_list.assert_awaited_once()


async def test_services_route_two_accounts_and_unload(hass, account):
    entry, client, _ = account
    other_client = MagicMock()
    other_client.lists = AsyncMock(
        return_value={"5": {"Id": "5", "Name": "Other", "Items": []}}
    )
    other_client.update_list = AsyncMock()
    other = MockConfigEntry(
        domain=DOMAIN, unique_id="account-b", data={"tokens": {}, CONF_LISTS: ["5"]}
    )
    other.add_to_hass(hass)
    with patch(
        "custom_components.lists_assistant.api.ListonicClient",
        return_value=other_client,
    ):
        assert await hass.config_entries.async_setup(other.entry_id)
    await call(hass, other, "rename_list", list_id="5", name="B")
    other_client.update_list.assert_awaited_once_with("5", Name="B")
    client.update_list.assert_not_awaited()
    with pytest.raises(ServiceValidationError):
        await call(hass, entry, "rename_list", list_id="5", name="wrong account")
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert all(hass.services.has_service(DOMAIN, name) for name in SERVICES)
    with pytest.raises(ServiceValidationError):
        await call(hass, entry, "get_lists", response=True)
    assert await call(hass, other, "get_lists", response=True)
    assert await hass.config_entries.async_unload(other.entry_id)
    assert all(not hass.services.has_service(DOMAIN, name) for name in SERVICES)


async def test_preflight_uses_fresh_snapshot(hass, account):
    entry, client, cloud = account
    # Cached coordinator still contains list 1; remote removal must block a write.
    cloud.pop("1")
    with pytest.raises(ServiceValidationError):
        await call(hass, entry, "delete_list", list_id="1")
    client.update_list.assert_not_awaited()
