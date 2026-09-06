"""Local operation must not require cloud discovery on every update."""

from custom_components.gemstone_lights.light import GemstoneLight


async def test_cloud_outage_keeps_known_local_controller_available(coordinator, vendor):
    # Given a successful online discovery with a reachable LAN controller.
    vendor.devices[0]["hub"] = {"localIp": "192.0.2.10", "tcpEnabled": True}
    coordinator.data = await coordinator._async_update_data()
    vendor.cloud_offline = True
    coordinator._discovery_next = None
    vendor.states["hub"] = {"onState": True, "colorB": {"value": 65280, "brightness": 80}}
    # When cloud rediscovery fails but the controller continues answering locally.
    coordinator.data = await coordinator._async_update_data()
    light = GemstoneLight(coordinator, "hub")
    # Then fresh local state remains available, including changes made outside HA.
    assert light.available
    assert light.rgbw_color == (0, 255, 0, 0)
    assert light.brightness == 80
