"""Run discovery and service behavior through real Home Assistant platforms."""

from homeassistant.helpers import entity_registry as er


async def test_new_controller_gets_every_platform_without_reload(hass, loaded_entry, vendor):
    # Given the integration is already loaded and another local controller is added.
    vendor.devices.append({"id": "garage", "name": "Garage", "online": True, "hub": {"localIp": "192.0.2.11", "tcpEnabled": True}})
    vendor.states["garage"] = {"onState": True, "colorB": {"value": 65280, "brightness": 80}}
    loaded_entry._discovery_next = None
    # When normal discovery refreshes and platform listeners process the new device.
    await loaded_entry.async_refresh()
    await hass.async_block_till_done()
    # Then all seven controller entities are registered and available without reloading.
    entities = [entity for entity in er.async_get(hass).entities.values() if entity.unique_id.startswith("garage_")]
    assert {entity.unique_id for entity in entities} == {
        "garage_light", "garage_speed", "garage_now_playing", "garage_design",
        "garage_pattern", "garage_library_folder", "garage_library_pattern",
    }
    assert all(hass.states.get(entity.entity_id).state != "unavailable" for entity in entities)


async def test_registered_library_service_respects_zone_entity_target(hass, loaded_entry, vendor):
    # Given real registered zone entities and a cached library pattern.
    front = er.async_get(hass).async_get_entity_id("light", "gemstone_lights", "hub_zone_front")
    # When Home Assistant dispatches the public integration action to the front zone.
    await hass.services.async_call("gemstone_lights", "play_library_pattern", {"entity_id": front, "pattern": "Holiday"}, blocking=True)
    # Then the complete palette reaches only the front zone and the back stays red.
    entries = {e["zoneId"]: e["pattern"] for e in vendor.writes[-1][3]["architectural"]["zonePatterns"]}
    assert entries["front"]["colors"] == [255, 65280]
    assert entries["back"]["colors"] == [255]
