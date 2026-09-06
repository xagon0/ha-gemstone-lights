"""Malformed responses must follow normal availability/fallback error paths."""

import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.gemstone_lights.api import GemstoneApiError
from custom_components.gemstone_lights.const import API_BASE_URL
from custom_components.gemstone_lights.local_api import (
    GemstoneLocalApi,
    GemstoneLocalError,
)


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"state": None},
        {"state": {"reported": {"currentlyPlaying": {}}}},
        {"state": {"reported": {"currentlyPlaying": {"onState": True, "pattern": []}}}},
        {
            "state": {
                "reported": {
                    "currentlyPlaying": {
                        "onState": True,
                        "architectural": {"zonePatterns": ["broken"]},
                    }
                }
            }
        },
    ],
)
async def test_malformed_local_state_uses_transport_error(hass, http, body):
    # Given valid JSON that cannot describe controller state.
    client = GemstoneLocalApi(async_get_clientsession(hass), "192.0.2.10")
    http.get("http://192.0.2.10/device-state/currently-playing", payload=body)
    # When state is read.
    with pytest.raises(GemstoneLocalError) as raised:
        await client.async_get_state()
    # Then it produces a fallback-compatible invalid-response error, never a fabricated off state.
    assert "invalid" in str(raised.value)


@pytest.mark.parametrize("body", [{}, {"data": None}, {"data": ["unexpected"]}])
async def test_malformed_cloud_catalog_cannot_clear_discovery(api, http, body):
    # Given a malformed cloud catalog response.
    http.get(f"{API_BASE_URL}/homegroup/list", payload=body)
    # When discovery reads the response.
    with pytest.raises(GemstoneApiError) as raised:
        await api.async_get_homegroups()
    # Then it reports an invalid catalog instead of an empty account.
    assert "invalid catalog" in str(raised.value)
