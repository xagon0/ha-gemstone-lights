"""Offline setup and operation through HA's real config and entity lifecycle."""

import pytest
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gemstone_lights.coordinator import GemstoneCoordinator


async def test_local_setup_needs_no_account_or_cloud(
    hass, vendor, http, enable_custom_integrations
):
    # Given a provisioned LAN controller and no Gemstone credentials or Cognito mock.
    flow = await hass.config_entries.flow.async_init(
        "gemstone_lights", context={"source": "user"}
    )
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"next_step_id": "local"}
    )
    # When it is added by address and its catalogs become due for refresh.
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"host": "192.0.2.10", "name": "Offline house"}
    )
    await hass.async_block_till_done()
    entry = result["result"]
    coordinator = entry.runtime_data
    coordinator._discovery_next = None
    coordinator._catalog_refreshed = None
    coordinator._library_refreshed = None
    await coordinator.async_refresh()
    await coordinator.async_play_color("local:192.0.2.10", 65280, 80)
    # Then physical commands use LAN and no authentication/discovery/catalog URL is contacted.
    assert entry.data["local_only"] is True
    assert "password" not in entry.data
    assert coordinator.device_available("local:192.0.2.10")
    assert vendor.states["hub"]["colorB"] == {"value": 65280, "brightness": 80}
    assert {url.host for _, url in http.requests} == {"192.0.2.10"}
    await hass.config_entries.async_unload(entry.entry_id)


async def test_local_failure_reports_unavailable_then_recovers_without_cloud(
    hass, vendor, http
):
    # Given an offline-only controller whose LAN connection fails.
    entry = MockConfigEntry(
        domain="gemstone_lights",
        data={
            "local_device": {
                "id": "hub",
                "hub": {"localIp": "192.0.2.10", "tcpEnabled": True},
            }
        },
    )
    entry.add_to_hass(hass)
    coordinator = GemstoneCoordinator(hass, entry, None)
    await coordinator._async_setup()
    await coordinator.async_refresh()
    vendor.failures["/device-state/currently-playing"] = 503
    # When a poll fails and a command is attempted during the outage.
    await coordinator.async_refresh()
    with pytest.raises(HomeAssistantError, match="cloud access is disabled"):
        await coordinator.async_set_power("hub", False)
    # Then the entity is unavailable, no cloud is contacted, and the next successful poll recovers.
    assert not coordinator.device_available("hub")
    vendor.failures.clear()
    await coordinator.async_refresh()
    assert coordinator.device_available("hub")
    assert vendor.writes == []
    assert {url.host for _, url in http.requests} == {"192.0.2.10"}
    await coordinator.async_shutdown()


async def test_existing_account_can_disable_cloud_without_losing_entities(
    hass, entry, loaded_entry, http
):
    # Given an account entry with previously cached zones and library patterns.
    old_ids = set(hass.states.async_entity_ids("light"))
    # When cloud access is disabled in options and HA reloads the integration.
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"local_only": True}
    )
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    http.requests.clear()
    coordinator._discovery_next = None
    coordinator._catalog_refreshed = None
    coordinator._library_refreshed = None
    await coordinator.async_refresh()
    # Then identities and offline content survive and expired catalogs cause no cloud traffic.
    assert result["type"] == "create_entry"
    assert coordinator.api is None
    assert set(hass.states.async_entity_ids("light")) == old_ids
    assert coordinator.library_size() == 1
    assert {zone["id"] for zone in coordinator.zones("hub")} == {"front", "back"}
    assert {url.host for _, url in http.requests} == {"192.0.2.10"}


async def test_local_setup_rejects_urls_before_network_io(
    hass, http, enable_custom_integrations
):
    # Given a local setup form with a URL where only an address is valid.
    flow = await hass.config_entries.flow.async_init(
        "gemstone_lights", context={"source": "user"}
    )
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"next_step_id": "local"}
    )
    # When the invalid host is submitted.
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"host": "https://example.invalid/path"}
    )
    # Then the user gets a field error and nothing is sent to that URL.
    assert result["errors"] == {"host": "invalid_host"}
    assert http.requests == {}
