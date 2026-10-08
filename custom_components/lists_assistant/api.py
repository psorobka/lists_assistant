"""Small async Listonic client; no Home Assistant dependency or secret logging."""

from __future__ import annotations

import asyncio
import base64
import logging
import math
from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import aiohttp

from .const import BASE_URL, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI

_LOGGER = logging.getLogger(__name__)


class ApiError(Exception):
    """The API could not complete an operation."""


class AuthError(ApiError):
    """The session needs user reauthentication."""


class ForbiddenError(ApiError):
    """Access to the resource was revoked."""


class NotFoundError(ApiError):
    """The requested resource does not exist."""


class AmbiguousWrite(ApiError):
    """A write may have reached the server. Do not blindly replay it."""


def identifier(value: Any) -> str:
    """Validate server identifiers before using them in request paths."""
    result = str(value)
    if not result or not result.isdecimal():
        raise ApiError("Invalid resource identifier")
    return result


def amount_text(value: Any) -> str:
    """Normalize quantities without float arithmetic or scientific notation."""
    if value == "":
        return ""
    try:
        number = Decimal(str(value).replace(",", "."))
    except InvalidOperation as err:
        raise ValueError("Invalid quantity") from err
    if not number.is_finite() or number < 0:
        raise ValueError("Invalid quantity")
    return format(number, "f")


class ListonicClient:
    """HTTP contract verified against the Listonic web API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        tokens: dict[str, Any] | None = None,
        on_tokens: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        *,
        base_url: str = BASE_URL,
    ) -> None:
        self.session = session
        self.tokens = dict(tokens or {})
        self.tokens.setdefault("device_id", str(uuid4()))
        self.on_tokens = on_tokens
        self.base_url = base_url.rstrip("/")
        self._refresh_lock = asyncio.Lock()

    def _headers(self, auth: bool = False) -> dict[str, str]:
        headers = {
            "DeviceId": self.tokens["device_id"],
            "Version": "web:4.0.0",
            "Culture": "pl",
            "Accept": "application/json",
        }
        if auth:
            credential = base64.b64encode(
                f"{CLIENT_ID}:{CLIENT_SECRET}".encode()
            ).decode()
            headers["clientauthorization"] = f"Bearer {credential}"
        elif self.tokens.get("access_token"):
            headers["Authorization"] = f"Bearer {self.tokens['access_token']}"
        return headers

    async def _exchange(self, provider: str, data: dict[str, str]) -> None:
        _LOGGER.debug("Exchanging Listonic credentials using %s", provider)
        try:
            async with self.session.post(
                f"{self.base_url}/api/loginextended",
                params={"provider": provider, "autoMerge": "1", "autoDestruct": "1"},
                data=data,
                headers=self._headers(auth=True),
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                if response.status in (400, 401, 403):
                    _LOGGER.warning(
                        "Listonic authentication was rejected (HTTP %s)",
                        response.status,
                    )
                    raise AuthError("Authentication rejected")
                if response.status != 200:
                    _LOGGER.error(
                        "Listonic authentication failed (HTTP %s)", response.status
                    )
                    raise ApiError(f"Authentication service HTTP {response.status}")
                payload = await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            _LOGGER.error(
                "Listonic authentication request failed: %s", type(err).__name__
            )
            raise ApiError("Authentication service unavailable") from err
        if not isinstance(payload, dict) or not payload.get("access_token"):
            _LOGGER.error("Listonic returned an invalid token response")
            raise ApiError("Invalid token response")
        # Some refresh responses omit refresh_token. Keep the previous one.
        self.tokens.update({k: v for k, v in payload.items() if v is not None})
        if self.on_tokens:
            await self.on_tokens(dict(self.tokens))
        if provider == "password":
            _LOGGER.info("Listonic authentication succeeded")
        else:
            _LOGGER.debug("Listonic access token refreshed")

    async def login(self, email: str, password: str) -> None:
        """Authenticate once; the caller must not persist the password."""
        await self._exchange(
            "password",
            {
                "username": email.strip(),
                "password": password,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "redirect_uri": REDIRECT_URI,
            },
        )

    async def refresh(self, rejected_token: str | None = None) -> None:
        """Coalesce simultaneous rejections and persist token rotation."""
        async with self._refresh_lock:
            if rejected_token and self.tokens.get("access_token") != rejected_token:
                return
            if not self.tokens.get("refresh_token"):
                raise AuthError("No refresh token")
            await self._exchange(
                "refresh_token", {"refresh_token": self.tokens["refresh_token"]}
            )

    async def request(
        self, method: str, path: str, *, json: Any = None, params: Any = None
    ) -> Any:
        """Read retries are bounded. Uncertain writes are never replayed."""
        if not self.tokens.get("access_token"):
            await self.refresh()
        refreshed = False
        for attempt in range(3):
            token = self.tokens.get("access_token")
            _LOGGER.debug(
                "Listonic API request: %s %s (attempt %s)",
                method,
                path,
                attempt + 1,
            )
            try:
                async with self.session.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=self._headers(),
                    json=json,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as response:
                    if response.status == 401:
                        if refreshed:
                            _LOGGER.error(
                                "Listonic rejected the session after token refresh"
                            )
                            raise AuthError("Session rejected after refresh")
                        _LOGGER.debug(
                            "Listonic rejected the access token; refreshing it"
                        )
                        await self.refresh(token)
                        refreshed = True
                        continue
                    if response.status == 403:
                        _LOGGER.warning("Listonic denied access to %s %s", method, path)
                        raise ForbiddenError("Access denied")
                    if response.status == 404:
                        _LOGGER.warning(
                            "Listonic resource was not found: %s %s", method, path
                        )
                        raise NotFoundError("Resource not found")
                    if response.status == 429 or response.status >= 500:
                        _LOGGER.warning(
                            "Listonic API returned HTTP %s for %s %s",
                            response.status,
                            method,
                            path,
                        )
                        if method != "GET":
                            if response.status >= 500:
                                raise AmbiguousWrite("Write result is unknown")
                            raise ApiError("Rate limited")
                        if attempt == 2:
                            raise ApiError("Server unavailable or rate limited")
                        try:
                            delay = float(
                                response.headers.get("Retry-After", 2**attempt)
                            )
                        except ValueError:
                            delay = 2**attempt
                        if not math.isfinite(delay) or delay > 30:
                            raise ApiError("Retry deferred by server")
                        await asyncio.sleep(max(0, delay))
                        continue
                    if response.status not in (200, 201, 204):
                        _LOGGER.error(
                            "Listonic API request failed with HTTP %s: %s %s",
                            response.status,
                            method,
                            path,
                        )
                        raise ApiError(f"API HTTP {response.status}")
                    body = await response.read()
                    if not body:
                        return None
                    return await response.json()
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                _LOGGER.warning(
                    "Listonic API request failed: %s %s (%s)",
                    method,
                    path,
                    type(err).__name__,
                )
                if method != "GET":
                    raise AmbiguousWrite("Write result is unknown") from err
                raise ApiError("API unavailable or invalid response") from err
        raise ApiError("Request retry limit reached")

    async def profile(self) -> dict[str, Any]:
        data = await self.request("GET", "/api/account/userinfo")
        if not isinstance(data, dict) or not data.get("Username"):
            raise ApiError("Missing account identity")
        return data

    async def lists(self) -> dict[str, dict[str, Any]]:
        data = await self.request(
            "GET",
            "/api/lists",
            params={
                "includeShares": "true",
                "archive": "false",
                "includeItems": "true",
            },
        )
        if not isinstance(data, list):
            raise ApiError("Invalid list response")
        result = {}
        for value in data:
            if not isinstance(value, dict):
                raise ApiError("Invalid list entry")
            list_id = identifier(value.get("Id", ""))
            if value.get("Active", 1) and not value.get("Deleted", 0):
                # Missing Items must not masquerade as a legitimately empty list.
                if not isinstance(value.get("Items"), list):
                    value["Items"] = await self.request(
                        "GET", f"/api/lists/{list_id}/items"
                    )
                if not isinstance(value["Items"], list):
                    raise ApiError("Invalid item response")
                for item in value["Items"]:
                    if not isinstance(item, dict):
                        raise ApiError("Invalid item entry")
                    identifier(item.get("Id", ""))
                    if not item.get("Deleted") and (
                        not isinstance(item.get("Name"), str)
                        or not item["Name"].strip()
                    ):
                        raise ApiError("Invalid item name")
                value["Items"] = [i for i in value["Items"] if not i.get("Deleted")]
                result[list_id] = value
        return result

    @staticmethod
    def _path(list_id: str, item_id: str | None = None) -> str:
        path = f"/api/lists/{quote(identifier(list_id))}"
        if item_id is not None:
            path += f"/items/{quote(identifier(item_id))}"
        return path

    async def create_list(self, name: str) -> dict[str, Any]:
        return await self.request(
            "POST", "/api/lists", json={"Name": name, "SortMode": 0}
        )

    async def update_list(self, list_id: str, **fields: Any) -> None:
        await self.request(
            "PATCH", self._path(list_id), json={**fields, "Id": identifier(list_id)}
        )

    async def add_item(self, list_id: str, name: str, **fields: Any) -> dict[str, Any]:
        if "amount" in fields:
            fields["amount"] = amount_text(fields["amount"])
        return await self.request(
            "POST", self._path(list_id) + "/items", json={"name": name, **fields}
        )

    async def update_item(self, list_id: str, item_id: str, **fields: Any) -> None:
        if "amount" in fields:
            fields["amount"] = amount_text(fields["amount"])
        await self.request("PATCH", self._path(list_id, item_id), json=fields)

    async def delete_item(self, list_id: str, item_id: str) -> None:
        await self.request("DELETE", self._path(list_id, item_id))
