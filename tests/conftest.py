"""Fixtures mock external I/O only; integration logic runs unchanged."""

import base64
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aioresponses import aioresponses
from aiohttp import ClientResponse
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gemstone_lights.api import GemstoneApi
from custom_components.gemstone_lights.coordinator import GemstoneCoordinator
from .vendor import Vendor

pytest_plugins = ["pytest_homeassistant_custom_component"]


class HTTPResponse(ClientResponse):
    """Bridge aioresponses 0.7.9 to aiohttp 3.14's new writer argument."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, stream_writer=SimpleNamespace(output_size=0), **kwargs)


@pytest.fixture
def http():
    """Intercept only HTTP requests; reject unregistered external access."""
    with patch("aioresponses.core.ClientResponse", HTTPResponse), aioresponses() as mocked:
        yield mocked


@pytest.fixture
def entry(hass):
    result = MockConfigEntry(
        domain="gemstone_lights",
        data={"email": "test@example.invalid", "password": "test-password"},
        unique_id="test@example.invalid",
    )
    result.add_to_hass(hass)
    return result


@pytest.fixture
async def api(hass):
    result = GemstoneApi(
        hass, async_get_clientsession(hass), "test@example.invalid", "test-password"
    )
    payload = base64.urlsafe_b64encode(json.dumps({"exp": time.time() + 3600}).encode()).decode()
    result._store({"access_token": f"header.{payload}.signature", "refresh_token": "refresh"})
    return result


@pytest.fixture
def vendor(http):
    return Vendor(http)


@pytest.fixture
async def coordinator(hass, entry, api, vendor):
    result = GemstoneCoordinator(hass, entry, api, enable_library=False)
    result._device_ids = ["hub"]
    result._zones = {"hub": [
        {"id": "front", "name": "Front", "lights": [3, 10, 12]},
        {"id": "back", "name": "Back", "lights": [3, 13, 15]},
    ]}
    result.data = {"devices": {"hub": {
        "info": {"id": "hub", "name": "House", "online": True},
        "state": {"onState": True, "color": 255},
    }}}
    entry.runtime_data = result
    yield result
    await result.async_shutdown()


@pytest.fixture
def cognito_external(monkeypatch):
    """Replace the external Cognito SDK login, not the integration's auth logic."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    payload = base64.urlsafe_b64encode(json.dumps({"exp": time.time() + 3600}).encode()).decode()
    def authenticate(user, password):
        user.access_token = f"header.{payload}.signature"
        user.refresh_token = "refresh"
        user.id_token = user.access_token
    monkeypatch.setattr("pycognito.Cognito.authenticate", authenticate)


@pytest.fixture
async def loaded_entry(hass, entry, vendor, hass_storage, enable_custom_integrations, cognito_external):
    """Load real platforms from a prior successful discovery saved in HA storage."""
    vendor.devices[0]["hub"] = {"localIp": "192.0.2.10", "tcpEnabled": True}
    key = f"gemstone_lights.{entry.entry_id}"
    hass_storage[key] = {"version": 1, "minor_version": 1, "key": key, "data": {
        "devices": vendor.devices, "zones": vendor.zones,
        "library": {"folder": [{"name": "Holiday", "data": {"name": "Holiday", "colors": [255, 65280], "animation": "chase"}}]},
        "library_folders": {"folder": {"id": "folder", "folderName": "Christmas", "category": "holidays"}},
    }}
    if not await hass.config_entries.async_setup(entry.entry_id):
        raise AssertionError("Integration setup failed")
    await hass.async_block_till_done()
    yield entry.runtime_data
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
