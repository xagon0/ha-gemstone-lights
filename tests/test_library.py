"""Library actions must respect zone targeting and preserve complete palettes."""

from custom_components.gemstone_lights.light import GemstoneZoneLight


async def test_library_action_targets_only_the_selected_zone(coordinator, vendor):
    # Given the whole run is red and the library contains a multi-color animated pattern.
    pattern = {
        "name": "Holiday",
        "colors": [65280, 4278190080],
        "animation": "chase",
        "speed": 17,
    }
    coordinator._library = {"folder": [{"name": "Holiday", "data": pattern}]}
    front = GemstoneZoneLight(coordinator, "hub", "front")
    # When the library action targets the front zone.
    await front.async_play_library_pattern("Holiday")
    # Then only front receives the library pattern, and back retains the previous red output.
    entries = {
        e["zoneId"]: e["pattern"]
        for e in vendor.writes[-1][3]["architectural"]["zonePatterns"]
    }
    assert entries["front"] == pattern
    assert entries["back"]["colors"] == [255]
    assert entries["back"]["animation"] == "motionless"
