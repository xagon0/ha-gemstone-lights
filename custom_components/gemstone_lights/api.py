"""Client for the Gemstone Lights cloud API.

Authentication is AWS Cognito (user pool, SRP). Every request then carries a
plain ``Authorization: Bearer <access token>`` header -- no AWS request signing
is involved.

Two details are easy to get wrong and are the reason this client exists:

* All control (write) calls use HTTP ``PUT``. Using ``POST`` returns a
  confusing AWS SigV4 error that suggests the endpoint needs request signing.
* Most endpoints expect ``deviceOrGroupId``; the architectural (per-zone)
  endpoints expect ``deviceId``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any

import aiohttp
from botocore.config import Config
from botocore.exceptions import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    API_BASE_URL,
    APP_HEADERS,
    AWS_REGION,
    COGNITO_CLIENT_ID,
    COGNITO_USER_POOL_ID,
    REQUEST_TIMEOUT,
)
from .validation import validate_design, validate_pattern, validate_state

_LOGGER = logging.getLogger(__name__)

# Refresh the access token this many seconds before it actually expires.
_TOKEN_LEEWAY = 120


class GemstoneError(HomeAssistantError):
    """Base error for this integration."""


class GemstoneAuthError(GemstoneError):
    """Raised when credentials are rejected."""


class GemstoneApiError(GemstoneError):
    """Raised when the API returns an unexpected response."""


def _jwt_expiry(token: str) -> float:
    """Return the ``exp`` claim of a JWT, or 0 if it cannot be read."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception:  # noqa: BLE001 - malformed token is not fatal
        return 0.0


class GemstoneApi:
    """Async client for the Gemstone Lights cloud."""

    def __init__(
        self,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
    ) -> None:
        """Initialise the client."""
        self._hass = hass
        self._session = session
        self._email = email
        self._password = password

        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._id_token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    # -- authentication -----------------------------------------------------

    def _login_sync(self) -> dict[str, str]:
        """Perform a full SRP login. Runs in an executor (pycognito is sync)."""
        from pycognito import Cognito  # noqa: PLC0415 - imported lazily

        user = Cognito(
            COGNITO_USER_POOL_ID,
            COGNITO_CLIENT_ID,
            user_pool_region=AWS_REGION,
            username=self._email,
            botocore_config=Config(
                connect_timeout=5, read_timeout=10, retries={"max_attempts": 1}
            ),
        )
        user.authenticate(password=self._password)
        return {
            "access_token": user.access_token,
            "refresh_token": user.refresh_token,
            "id_token": user.id_token,
        }

    def _refresh_sync(self) -> dict[str, str]:
        """Renew the access token using the refresh token."""
        from pycognito import Cognito  # noqa: PLC0415

        user = Cognito(
            COGNITO_USER_POOL_ID,
            COGNITO_CLIENT_ID,
            user_pool_region=AWS_REGION,
            id_token=self._id_token,
            access_token=self._access_token,
            refresh_token=self._refresh_token,
            botocore_config=Config(
                connect_timeout=5, read_timeout=10, retries={"max_attempts": 1}
            ),
        )
        user.renew_access_token()
        return {
            "access_token": user.access_token,
            "refresh_token": user.refresh_token or self._refresh_token,
            "id_token": user.id_token,
        }

    def _store(self, tokens: dict[str, str]) -> None:
        self._access_token = tokens["access_token"]
        self._refresh_token = tokens.get("refresh_token")
        self._id_token = tokens.get("id_token")
        self._expires_at = _jwt_expiry(self._access_token or "")

    async def async_login(self) -> None:
        """Log in with the configured credentials."""
        try:
            tokens = await self._hass.async_add_executor_job(self._login_sync)
        except ClientError as err:
            code = err.response.get("Error", {}).get("Code")
            if code in {
                "NotAuthorizedException",
                "UserNotFoundException",
                "UserNotConfirmedException",
                "PasswordResetRequiredException",
            }:
                raise GemstoneAuthError(
                    "Gemstone rejected the account credentials"
                ) from err
            raise GemstoneApiError(
                "Gemstone authentication is temporarily unavailable"
            ) from err
        except Exception as err:
            raise GemstoneApiError(
                "Could not connect to Gemstone authentication"
            ) from err
        self._store(tokens)
        _LOGGER.debug("Gemstone login succeeded for %s", self._email)

    async def _async_ensure_token(self, rejected_token: str | None = None) -> None:
        """Make sure a usable access token is available."""
        async with self._lock:
            if rejected_token is not None and self._access_token == rejected_token:
                self._expires_at = 0.0
            if self._access_token and time.time() < self._expires_at - _TOKEN_LEEWAY:
                return

            if self._refresh_token:
                try:
                    tokens = await self._hass.async_add_executor_job(self._refresh_sync)
                    self._store(tokens)
                    return
                except ClientError as err:
                    if (
                        err.response.get("Error", {}).get("Code")
                        != "NotAuthorizedException"
                    ):
                        raise GemstoneApiError(
                            "Could not renew the Gemstone session"
                        ) from err
                    _LOGGER.debug("Refresh token expired; signing in again")
                except Exception as err:
                    raise GemstoneApiError(
                        "Could not renew the Gemstone session"
                    ) from err

            await self.async_login()

    # -- transport ----------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        _retry: bool = True,
    ) -> Any:
        """Send a request and return the decoded ``data`` payload."""
        await self._async_ensure_token()

        access_token = self._access_token
        headers = {**APP_HEADERS, "authorization": f"Bearer {access_token}"}
        url = f"{API_BASE_URL}{path}"

        try:
            async with self._session.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                text = await resp.text()

                if resp.status in (401, 403) and _retry:
                    # Token may have been revoked; force a fresh login once.
                    _LOGGER.debug("Auth rejected on %s, retrying with new token", path)
                    await self._async_ensure_token(rejected_token=access_token)
                    return await self._request(
                        method, path, params=params, json_body=json_body, _retry=False
                    )

                if resp.status in (401, 403):
                    raise GemstoneAuthError("Gemstone rejected the renewed session")

                if resp.status >= 400:
                    raise GemstoneApiError(
                        f"{method} {path} returned HTTP {resp.status}: {text[:200]}"
                    )

                if not text:
                    if method == "GET":
                        raise GemstoneApiError(f"{path} returned an empty response")
                    return None
                try:
                    body = json.loads(text)
                except ValueError as err:
                    raise GemstoneApiError(
                        f"{method} {path} returned invalid JSON"
                    ) from err

                data = body.get("data") if isinstance(body, dict) else body
                if method == "GET":
                    if path == "/deviceControl/currentlyPlaying":
                        try:
                            return validate_state(data)
                        except ValueError as err:
                            raise GemstoneApiError(
                                f"{path} returned invalid state"
                            ) from err
                    if not isinstance(data, list) or any(
                        not isinstance(item, dict) for item in data
                    ):
                        raise GemstoneApiError(f"{path} returned an invalid catalog")
                    try:
                        for item in data:
                            if (
                                path == "/homegroup/devices"
                                and item.get("hub") is not None
                                and not isinstance(item["hub"], dict)
                            ):
                                raise ValueError("Invalid hub metadata")
                            if path == "/deviceControl/architectural/list":
                                validate_design(item)
                            if item.get("patternData") is not None:
                                validate_pattern(item["patternData"])
                    except ValueError as err:
                        raise GemstoneApiError(
                            f"{path} returned invalid catalog content"
                        ) from err
                return data
        except asyncio.TimeoutError as err:
            raise GemstoneApiError(f"Timeout calling {method} {path}") from err
        except aiohttp.ClientError as err:
            raise GemstoneApiError(f"Error calling {method} {path}: {err}") from err

    # -- read endpoints -----------------------------------------------------

    async def async_get_homegroups(self) -> list[dict[str, Any]]:
        """Return the homegroups belonging to the account."""
        return await self._request("GET", "/homegroup/list") or []

    async def async_get_devices(self, homegroup_id: str) -> list[dict[str, Any]]:
        """Return the controllers in a homegroup."""
        return (
            await self._request(
                "GET", "/homegroup/devices", params={"homegroupId": homegroup_id}
            )
            or []
        )

    async def async_get_state(self, device_id: str) -> dict[str, Any]:
        """Return what the controller is currently playing."""
        return (
            await self._request(
                "GET",
                "/deviceControl/currentlyPlaying",
                params={"deviceOrGroupId": device_id},
            )
            or {}
        )

    async def async_get_zones(self, device_id: str) -> list[dict[str, Any]]:
        """Return the configured zones (e.g. Front Upper, Rear Lower)."""
        return (
            await self._request(
                "GET", "/deviceControl/zone/list", params={"deviceId": device_id}
            )
            or []
        )

    async def async_get_designs(self, device_id: str) -> list[dict[str, Any]]:
        """Return saved architectural designs (these can be per-zone)."""
        return (
            await self._request(
                "GET",
                "/deviceControl/architectural/list",
                params={"deviceId": device_id},
            )
            or []
        )

    async def async_get_folders(self) -> list[dict[str, Any]]:
        """Return the account's pattern folders."""
        return await self._request("GET", "/folders/list") or []

    async def async_get_folder_patterns(self, folder_id: str) -> list[dict[str, Any]]:
        """Return the patterns inside a folder."""
        return (
            await self._request(
                "GET", "/folders/pattern/list", params={"folderId": folder_id}
            )
            or []
        )

    # -- write endpoints (always PUT) --------------------------------------

    async def async_set_power(self, device_id: str, on: bool) -> None:
        """Turn the lights on or off."""
        await self._request(
            "PUT",
            "/deviceControl/onState",
            params={"deviceOrGroupId": device_id},
            json_body={"onState": on},
        )

    async def async_play_color(self, device_id: str, color: int) -> None:
        """Show a single solid colour (0xRRGGBB)."""
        await self._request(
            "PUT",
            "/deviceControl/play/color",
            params={"deviceOrGroupId": device_id},
            json_body={"color": color},
        )

    async def async_play_pattern(self, device_id: str, pattern: dict[str, Any]) -> None:
        """Play a whole-controller pattern."""
        await self._request(
            "PUT",
            "/deviceControl/play/pattern",
            params={"deviceOrGroupId": device_id},
            json_body={"pattern": pattern},
        )

    async def async_get_library_folders(self) -> list[dict[str, Any]]:
        """Return Gemstone's official pattern folders."""
        return (
            await self._request(
                "GET",
                "/downloads/folders/listGemstoneManaged",
                params={"page": 1, "pageSize": 500},
            )
            or []
        )

    async def async_get_library_patterns(self) -> list[dict[str, Any]]:
        """Return every pattern in Gemstone's official library.

        The endpoint pages; a short page means the end.
        """
        page_size = 750
        patterns: list[dict[str, Any]] = []
        for page in range(1, 21):
            batch = (
                await self._request(
                    "GET",
                    "/downloads/folders/pattern/listGemstoneManaged",
                    params={"page": page, "pageSize": page_size},
                )
                or []
            )
            patterns.extend(batch)
            if len(batch) < page_size:
                break
        return patterns

    async def async_set_local_enabled(self, device_id: str, enabled: bool) -> None:
        """Turn the controller's "Allow Local Commands" switch on or off.

        This is the same switch as Advanced Settings in the Gemstone app; the
        controller opens or closes its HTTP port in response.
        """
        await self._request(
            "PUT",
            "/deviceControl/deviceSettings",
            params={"deviceId": device_id},
            json_body={"tcpEnabled": enabled},
        )

    async def async_play_design(self, device_id: str, design: dict[str, Any]) -> None:
        """Play a saved architectural design (note: uses ``deviceId``)."""
        payload = {**design, "preview": False}
        await self._request(
            "PUT",
            "/deviceControl/play/architectural",
            params={"deviceId": device_id},
            json_body={"architectural": payload},
        )
