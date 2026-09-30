"""Opt-in live contract check, restricted to a newly created disposable list.

Run from the repository root with `python -m scripts.live_api_probe`.
Credentials are read interactively; neither credentials nor responses are logged.
"""

import asyncio
import getpass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import aiohttp

from custom_components.lists_assistant.api import ListonicClient, identifier


async def probe(email: str, password: str) -> None:
    """Verify persisted refresh credentials and CRUD without touching existing lists."""
    async with aiohttp.ClientSession() as session:
        client = ListonicClient(session)
        await client.login(email, password)
        print("PASS login", flush=True)
        account = (await client.profile())["Username"]
        print("PASS account identity", flush=True)
        restored = ListonicClient(
            session,
            {key: client.tokens[key] for key in ("refresh_token", "device_id")},
        )
        await restored.refresh()
        assert (await restored.profile())["Username"] == account
        client = restored
        print("PASS new client session restored using refresh token only", flush=True)
        await client.lists()
        print("PASS list snapshot", flush=True)
        name = f"HA API test {datetime.now(UTC):%Y%m%d-%H%M%S} {uuid4().hex[:8]}"
        created = await client.create_list(name)
        list_id = identifier(created["Id"])
        print("PASS create disposable list", flush=True)
        try:
            await client.update_list(list_id, Id=list_id, Name=name + " renamed")
            lists = await client.lists()
            assert lists[list_id]["Name"] == name + " renamed"
            print("PASS rename list and read back", flush=True)
            item = await client.add_item(
                list_id,
                "HA test milk",
                amount="2.5",
                unit="l",
                description="Disposable API test",
                price=3.49,
            )
            item_id = identifier(item["Id"])
            items = (await client.lists())[list_id]["Items"]
            saved = next(value for value in items if str(value["Id"]) == item_id)
            assert saved["Name"] == "HA test milk"
            assert Decimal(str(saved["Amount"])) == Decimal("2.5")
            assert saved["Unit"] == "l"
            assert saved["Description"] == "Disposable API test"
            assert Decimal(str(saved["Price"])) == Decimal("3.49")
            print(
                "PASS create item with amount, unit, description and price", flush=True
            )
            await client.update_item(
                list_id, item_id, name="HA test renamed", checked=1
            )
            saved = next(
                value
                for value in (await client.lists())[list_id]["Items"]
                if str(value["Id"]) == item_id
            )
            assert saved["Name"] == "HA test renamed" and saved["Checked"] == 1
            for field in ("Amount", "Unit", "Price", "Description"):
                assert (
                    saved[field]
                    == next(v for v in items if str(v["Id"]) == item_id)[field]
                )
            print("PASS atomic rename/check preserves item metadata", flush=True)
            await client.update_item(list_id, item_id, checked=0, description="")
            saved = next(
                value
                for value in (await client.lists())[list_id]["Items"]
                if str(value["Id"]) == item_id
            )
            assert saved["Checked"] == 0 and not saved.get("Description")
            print("PASS uncheck and clear description", flush=True)
            await client.delete_item(list_id, item_id)
            assert not (await client.lists())[list_id]["Items"]
            print("PASS delete item and read back", flush=True)
        finally:
            await client.update_list(list_id, Id=list_id, Active=0)
            assert list_id not in await client.lists()
            print("PASS cleanup: disposable list deactivated and absent", flush=True)


if __name__ == "__main__":
    user = input("Listonic email: ")
    secret = getpass.getpass("Listonic password: ")
    try:
        asyncio.run(probe(user, secret))
    except Exception as error:
        # Never dump tracebacks or raw HTTP payloads from an authenticated request.
        print(f"FAIL live probe: {type(error).__name__}", flush=True)
        raise SystemExit(1) from None
    finally:
        secret = None
