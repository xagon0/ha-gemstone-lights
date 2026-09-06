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
