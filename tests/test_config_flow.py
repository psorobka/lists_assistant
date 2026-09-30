"""Config-flow tests; the Listonic account is always an HTTP-free fixture."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lists_assistant.api import ApiError, AuthError
from custom_components.lists_assistant.const import CONF_BRIDGE, CONF_LISTS, DOMAIN


@pytest.fixture(autouse=True)
def setup_after_flow():
    """The real entry lifecycle is exercised separately in test_lifecycle."""
    with patch(
        "custom_components.lists_assistant.async_setup_entry", return_value=True
    ):
        yield


@pytest.fixture
def auth_client():
    with patch(
        "custom_components.lists_assistant.config_flow.ListonicClient"
    ) as factory:
        client = factory.return_value
        client.login = AsyncMock()
        client.profile = AsyncMock(return_value={"Username": "demo@example.test"})
        client.lists = AsyncMock(return_value={"123": {"Name": "Zakupy", "Items": []}})
        client.tokens = {"access_token": "access", "refresh_token": "refresh"}
        yield client


async def login_flow(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": "demo@example.test", CONF_PASSWORD: "test-only"}
    )


@pytest.mark.asyncio
async def test_login_then_select_list(hass: HomeAssistant, native_shopping) -> None:
    """Authenticate, select lists, and retain tokens but never the password."""
    with patch(
        "custom_components.lists_assistant.config_flow.ListonicClient"
    ) as client_type:
        client = client_type.return_value
        client.login = AsyncMock()
        client.profile = AsyncMock(return_value={"Username": "demo@example.test"})
        client.lists = AsyncMock(return_value={"123": {"Name": "Zakupy", "Items": []}})
        client.tokens = {"access_token": "access", "refresh_token": "refresh"}

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["step_id"] == "user"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"email": "demo@example.test", CONF_PASSWORD: "temporary-password"},
        )
        assert result["step_id"] == "lists"
        client.login.assert_awaited_once_with("demo@example.test", "temporary-password")

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_LISTS: ["123"], CONF_BRIDGE: "123"}
        )
        assert result["step_id"] == "bridge"
        assert result["description_placeholders"]["local_count"] == "0"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"confirm": True}
        )

    assert result["type"] == "create_entry"
    assert result["data"][CONF_LISTS] == ["123"]
    assert result["data"][CONF_BRIDGE] == "123"
    assert CONF_PASSWORD not in result["data"]


@pytest.mark.asyncio
async def test_invalid_password_shows_auth_error(hass: HomeAssistant) -> None:
    """Bad credentials leave the flow at its first step."""
    from custom_components.lists_assistant.api import AuthError

    with patch(
        "custom_components.lists_assistant.config_flow.ListonicClient"
    ) as client_type:
        client_type.return_value.login = AsyncMock(side_effect=AuthError("invalid"))
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"email": "demo@example.test", CONF_PASSWORD: "wrong"}
        )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_invalid_email_rejected_before_network(hass, auth_client):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": "invalid email", CONF_PASSWORD: "test-only"}
    )
    assert result["errors"] == {"email": "invalid_email"}
    auth_client.login.assert_not_awaited()


async def test_connection_error_keeps_email(hass, auth_client):
    auth_client.login.side_effect = ApiError("offline")
    result = await login_flow(hass)
    assert result["errors"] == {"base": "cannot_connect"}
    email_field = next(
        key for key in result["data_schema"].schema if key.schema == "email"
    )
    assert email_field.default() == "demo@example.test"


@pytest.mark.parametrize(
    "selection,bridge,error",
    [([], "none", "select_list"), (["123"], "999", "invalid_selection")],
)
async def test_invalid_list_selection(hass, auth_client, selection, bridge, error):
    result = await login_flow(hass)
    # Direct invocation also verifies validation of input supplied by an API client.
    flow = hass.config_entries.flow._progress[result["flow_id"]]
    result = await flow.async_step_lists({CONF_LISTS: selection, CONF_BRIDGE: bridge})
    assert result["errors"] == {"base": error}


async def test_empty_account_has_actionable_abort(hass, auth_client):
    auth_client.lists.return_value = {}
    result = await login_flow(hass)
    assert result["type"] == "abort" and result["reason"] == "no_lists"


async def test_duplicate_account(hass, auth_client):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="demo@example.test", data={})
    entry.add_to_hass(hass)
    result = await login_flow(hass)
    assert result["type"] == "abort" and result["reason"] == "already_configured"


@pytest.mark.parametrize("wrong_account", [False, True])
async def test_reauth_preserves_account_and_selection(hass, auth_client, wrong_account):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="demo@example.test",
        title="Existing",
        data={
            "username": "demo@example.test",
            "tokens": {"refresh_token": "old"},
            CONF_LISTS: ["123"],
            CONF_BRIDGE: "123",
        },
    )
    entry.add_to_hass(hass)
    original = dict(entry.data)
    if wrong_account:
        auth_client.profile.return_value = {"Username": "other@example.test"}
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"email": "demo@example.test", CONF_PASSWORD: "test-only"},
    )
    if wrong_account:
        assert result["errors"] == {"base": "wrong_account"}
        assert entry.data == original
    else:
        assert result["type"] == "abort" and result["reason"] == "reauth_successful"
        assert entry.data["tokens"] == auth_client.tokens
        assert entry.data[CONF_LISTS] == ["123"]
        assert entry.data[CONF_BRIDGE] == "123"
        assert entry.unique_id == "demo@example.test"
        assert CONF_PASSWORD not in entry.data
    auth_client.lists.assert_not_awaited()


async def test_reauth_rejected_credentials(hass, auth_client):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="demo@example.test", data={})
    entry.add_to_hass(hass)
    auth_client.login.side_effect = AuthError("rejected")
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": entry.entry_id},
        data=entry.data,
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"email": "demo@example.test", CONF_PASSWORD: "test-only"},
    )
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_merge_needs_native_list_and_confirmation(hass, auth_client):
    result = await login_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_LISTS: ["123"],
            CONF_BRIDGE: "123",
        },
    )
    assert result["step_id"] == "bridge"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"confirm": True}
    )
    assert result["errors"] == {"base": "shopping_list_missing"}


async def test_merge_checkbox_is_explicit(hass, auth_client, native_shopping):
    result = await login_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_LISTS: ["123"],
            CONF_BRIDGE: "123",
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"confirm": False}
    )
    assert result["errors"] == {"base": "confirm_merge"}


async def test_other_account_cannot_claim_shopping_list(hass, auth_client):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="other@example.test", data={CONF_BRIDGE: "99"}
    )
    entry.add_to_hass(hass)
    result = await login_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_LISTS: ["123"],
            CONF_BRIDGE: "123",
        },
    )
    assert result["errors"] == {"base": "bridge_in_use"}


async def test_unbound_account_finishes_without_merge(hass, auth_client):
    result = await login_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_LISTS: ["123"],
            CONF_BRIDGE: "none",
        },
    )
    assert result["type"] == "create_entry"
