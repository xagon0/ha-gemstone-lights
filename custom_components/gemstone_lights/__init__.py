"""The Gemstone Lights integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import GemstoneApi, GemstoneAuthError, GemstoneError
from .const import (
    CONF_EMAIL,
    CONF_ENABLE_LOCAL,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PREFER_LOCAL,
    DOMAIN,
)
from .coordinator import GemstoneCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
]

type GemstoneConfigEntry = ConfigEntry[GemstoneCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: GemstoneConfigEntry) -> bool:
    """Set up Gemstone Lights from a config entry."""
    api = GemstoneApi(
        hass,
        async_get_clientsession(hass),
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
    )

    try:
        await api.async_login()
    except GemstoneAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except GemstoneError as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = GemstoneCoordinator(
        hass,
        entry,
        api,
        host_override=entry.options.get(CONF_HOST) or None,
        prefer_local=entry.options.get(CONF_PREFER_LOCAL, True),
        enable_local=entry.options.get(CONF_ENABLE_LOCAL, True),
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GemstoneConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: GemstoneConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
