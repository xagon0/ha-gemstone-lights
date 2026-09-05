"""Config flow for Gemstone Lights."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import GemstoneApi, GemstoneAuthError, GemstoneError
from .const import (
    CONF_EMAIL,
    CONF_ENABLE_LIBRARY,
    CONF_ENABLE_LOCAL,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PREFER_LOCAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str}
)


OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_PREFER_LOCAL, default=True): bool,
        vol.Optional(CONF_ENABLE_LOCAL, default=True): bool,
        vol.Optional(CONF_ENABLE_LIBRARY, default=True): bool,
        vol.Optional(CONF_HOST, default=""): str,
    }
)


class GemstoneConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle setup, options and re-authentication."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return GemstoneOptionsFlow()

    def __init__(self) -> None:
        """Initialise the flow."""
        self._reauth_email: str | None = None

    async def _async_validate(self, email: str, password: str) -> str | None:
        """Return an error key, or None when the credentials work."""
        api = GemstoneApi(self.hass, async_get_clientsession(self.hass), email, password)
        try:
            await api.async_login()
            await api.async_get_homegroups()
        except GemstoneAuthError:
            return "invalid_auth"
        except GemstoneError:
            return "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error validating Gemstone credentials")
            return "unknown"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            await self.async_set_unique_id(email.lower())
            self._abort_if_unique_id_configured()

            error = await self._async_validate(email, user_input[CONF_PASSWORD])
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title=email,
                    data={CONF_EMAIL: email, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start re-authentication after the stored password stops working."""
        self._reauth_email = entry_data.get(CONF_EMAIL)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new password."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = self._reauth_email or user_input.get(CONF_EMAIL, "")
            error = await self._async_validate(email, user_input[CONF_PASSWORD])
            if error:
                errors["base"] = error
            else:
                entry = self._get_reauth_entry()
                return self.async_update_reload_and_abort(
                    entry,
                    data={**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"email": self._reauth_email or ""},
            errors=errors,
        )


class GemstoneOptionsFlow(OptionsFlow):
    """Let the user tune local control."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage local-control options."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_PREFER_LOCAL: user_input.get(CONF_PREFER_LOCAL, True),
                    CONF_ENABLE_LOCAL: user_input.get(CONF_ENABLE_LOCAL, True),
                    CONF_ENABLE_LIBRARY: user_input.get(CONF_ENABLE_LIBRARY, True),
                    CONF_HOST: (user_input.get(CONF_HOST) or "").strip(),
                }
            )

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, self.config_entry.options
            ),
        )
