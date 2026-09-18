"""A failed local command must retry through cloud without corrupting its payload."""

from datetime import timedelta
from unittest.mock import patch

import aiohttp
import pytest
from homeassistant.util import dt as dt_util


async def test_failed_local_color_write_falls_back_to_scaled_cloud_rgbw(
    coordinator, vendor
):
    # Given a controller was reachable locally but now rejects local writes.
    vendor.devices[0]["hub"] = {"localIp": "192.0.2.10", "tcpEnabled": True}
    coordinator.data = await coordinator._async_update_data()
    vendor.failures["/device-control/play"] = 503
    # When a dimmed white-channel color is requested.
    await coordinator.async_play_color("hub", 4278190080, 80)
    # Then cloud receives the correctly scaled payload and the failed LAN route enters backoff.
    assert vendor.writes[-1] == (
        "cloud",
        "hub",
        "/deviceControl/play/color",
        {"color": 1342177280},
    )
    assert not coordinator.is_local("hub")


async def test_lan_outage_keeps_entities_available_via_cloud(
    hass, loaded_entry, vendor
):
    # Given a fully loaded account integration whose local state endpoint now fails.
    vendor.failures["/device-state/currently-playing"] = 500
    # When HA refreshes and a user turns the whole controller off during the outage.
    await loaded_entry.async_refresh()
    lights = [
        s
        for s in hass.states.async_all("light")
        if s.entity_id.startswith("light.house")
    ]
    assert lights and all(s.state != "unavailable" for s in lights)
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.house"}, blocking=True
    )
    # Then the cloud receives the command and HA reports cloud control without losing availability.
    assert vendor.writes[-1][0] == "cloud"
    assert vendor.states["hub"]["onState"] is False
    assert hass.states.get("light.house").state == "off"
    assert hass.states.get("light.house").attributes["control"] == "cloud"


async def test_rebooted_lan_returns_after_backoff(hass, loaded_entry, vendor):
    # Given a failed LAN endpoint has caused a successful cloud fallback.
    vendor.failures["/device-state/currently-playing"] = 500
    await loaded_entry.async_refresh()
    assert hass.states.get("light.house").attributes["control"] == "cloud"
    # When LAN recovers and the retry deadline passes.
    vendor.failures.clear()
    loaded_entry._local_retry_after["hub"] = dt_util.utcnow() - timedelta(seconds=1)
    await loaded_entry.async_refresh()
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": "light.house"}, blocking=True
    )
    # Then HA returns to LAN and subsequent control uses the recovered local endpoint.
    assert hass.states.get("light.house").attributes["control"] == "local"
    assert vendor.writes[-1][0] == "local"
    assert vendor.states["hub"]["onState"] is False


@pytest.mark.parametrize(
    "failure", [aiohttp.ClientConnectionError("Connection refused"), TimeoutError()]
)
async def test_socket_failure_keeps_cloud_control(hass, loaded_entry, vendor, failure):
    # Given LAN transport refuses connections or stops responding while cloud remains healthy.
    request = aiohttp.ClientSession._request

    async def network(session, method, url, **kwargs):
        if str(url).startswith("http://192.0.2.10/"):
            raise failure
        return await request(session, method, url, **kwargs)

    # When HA polls and receives a normal light action during the LAN outage.
    with patch.object(aiohttp.ClientSession, "_request", network):
        await loaded_entry.async_refresh()
        await hass.services.async_call(
            "light", "turn_off", {"entity_id": "light.house"}, blocking=True
        )
    # Then cloud control remains available and actually sends the requested off command.
    state = hass.states.get("light.house")
    assert state.state == "off"
    assert state.attributes["control"] == "cloud"
    assert vendor.writes[-1][0] == "cloud"
    assert vendor.states["hub"]["onState"] is False
