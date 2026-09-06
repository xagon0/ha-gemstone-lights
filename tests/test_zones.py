"""Zone editing must preserve content outside the fields being changed."""

from copy import deepcopy

from custom_components.gemstone_lights.light import GemstoneZoneLight


async def test_editing_front_preserves_complete_back_pattern(coordinator, vendor):
    # Given the back zone has a multi-color pattern with vendor-specific settings.
    back = {"zoneId": "back", "vendorFlag": "keep", "pattern": {
        "id": "saved", "colors": [255, 65280], "animation": "chase", "speed": 17,
        "direction": 1, "brightness": 80, "backgroundColor": 4278190080,
        "vendorExtension": {"keep": 42},
    }}
    original = deepcopy(back)
    coordinator.data["devices"]["hub"]["state"] = {"onState": True, "architectural": {"zonePatterns": [back]}}
    # When only the front zone is changed to blue.
    await coordinator.async_set_zone("hub", "front", {"color": 16711680, "brightness": 200, "animation": "Solid"})
    # Then the full back entry survives the cloud write and the original input is not mutated.
    sent = vendor.writes[-1][3]["architectural"]["zonePatterns"]
    assert next(e for e in sent if e["zoneId"] == "back") == original
    assert next(e for e in sent if e["zoneId"] == "front")["pattern"]["colors"] == [16711680]
    assert back == original


async def test_dimming_a_zone_keeps_its_palette_and_animation_settings(coordinator, vendor):
    # Given a running two-color zone pattern.
    pattern = {"colors": [255, 65280], "animation": "chase", "speed": 17, "brightness": 200}
    coordinator.data["devices"]["hub"]["state"] = {"onState": True, "architectural": {"zonePatterns": [{"zoneId": "front", "pattern": pattern}]}}
    light = GemstoneZoneLight(coordinator, "hub", "front")
    # When only its brightness changes through the light entity.
    await light.async_turn_on(brightness=80)
    # Then brightness is the only field of the existing pattern that changes.
    sent = vendor.writes[-1][3]["architectural"]["zonePatterns"][0]["pattern"]
    assert sent == {**pattern, "brightness": 80}


async def test_turning_off_front_of_whole_run_preserves_back(coordinator, vendor):
    # Given the whole controller is playing a multi-color pattern.
    pattern = {"colors": [255, 65280], "animation": "chase", "speed": 17, "brightness": 200}
    coordinator.data["devices"]["hub"]["state"] = {"onState": True, "pattern": pattern}
    front = GemstoneZoneLight(coordinator, "hub", "front")
    back = GemstoneZoneLight(coordinator, "hub", "back")
    # When only the front zone is switched off.
    await front.async_turn_off()
    # Then the controller stays on, the back keeps the full pattern, and the front is off.
    sent = vendor.writes[-1][3]["architectural"]["zonePatterns"]
    assert sent == [{"zoneId": "back", "pattern": pattern}]
    assert back.is_on
    assert not front.is_on


async def test_three_explicit_pixels_cover_the_entire_zone(coordinator, vendor):
    # Given the local API reports exactly three explicit blue pixel indices.
    vendor.devices[0]["hub"] = {"localIp": "192.0.2.10", "tcpEnabled": True}
    vendor.states["hub"] = {"onState": True, "architectural": {"brightness": 80, "staticColors": [{"lights": [10, 11, 12], "color": 16711680}]}}
    # When the actual local response is decoded and mapped onto the configured zone.
    coordinator.data = await coordinator._async_update_data()
    front = GemstoneZoneLight(coordinator, "hub", "front")
    # Then pixel 10 is not mistaken for a range header, and the zone is lit blue at 80.
    assert front.is_on
    assert front.rgbw_color == (0, 0, 255, 0)
    assert front.brightness == 80


async def test_endpoints_alone_do_not_imply_uniform_zone_coverage(coordinator):
    # Given a static layout colors the endpoints but leaves the middle pixel unassigned.
    coordinator.data["devices"]["hub"]["state"] = {"onState": True, "architectural": {"staticColors": [{"lights": [10, 12], "color": 255}]}}
    # When zone state is derived from the explicit pixels.
    front = GemstoneZoneLight(coordinator, "hub", "front")
    # Then it must not report the whole zone as a uniform red.
    assert front.rgbw_color is None
