"""Regressions from real Hub2 cloud-zone brightness normalization."""

import asyncio
from copy import deepcopy

from custom_components.gemstone_lights.light import GemstoneLight, GemstoneZoneLight


async def test_cloud_zone_echo_preserves_logical_palette_and_brightness(
    coordinator, vendor
):
    # Given two animated zones with independent brightness and an RGBW palette.
    design = {
        "brightness": 255,
        "zonePatterns": [
            {
                "zoneId": "front",
                "pattern": {
                    "colors": [4278190335, 65280],
                    "backgroundColor": 4278190080,
                    "animation": "chase",
                    "brightness": 64,
                    "speed": 96,
                    "direction": 1,
                },
            },
            {
                "zoneId": "back",
                "pattern": {
                    "colors": [16711680],
                    "animation": "motionless",
                    "brightness": 80,
                },
            },
        ],
    }
    original = deepcopy(design)
    # When the cloud applies it, normalizes nested brightness to 255, and HA polls the result.
    await coordinator.async_play_design("hub", design)
    await asyncio.sleep(5.05)
    coordinator.data = await coordinator._async_update_data()
    # Then physical channels retain the requested dimming while HA keeps the original colors and levels.
    wire = vendor.states["hub"]["architectural"]["zonePatterns"]
    assert wire[0]["pattern"]["colors"] == [1073741888, 16384]
    assert wire[0]["pattern"]["backgroundColor"] == 1073741824
    assert wire[1]["pattern"]["colors"] == [5242880]
    front = GemstoneZoneLight(coordinator, "hub", "front")
    back = GemstoneZoneLight(coordinator, "hub", "back")
    assert front.brightness == 64
    assert front.rgbw_color == (255, 0, 0, 255)
    assert back.brightness == 80
    assert coordinator.device_state("hub")["architectural"] == {
        **original,
        "preview": False,
    }
    assert design == original


async def test_cloud_zone_dim_then_brighten_does_not_scale_twice(coordinator, vendor):
    # Given an animated front at 64 and a blue back at 80, already echoed by the cloud.
    await coordinator.async_play_design(
        "hub",
        {
            "brightness": 255,
            "zonePatterns": [
                {
                    "zoneId": "front",
                    "pattern": {
                        "colors": [255, 65280],
                        "animation": "chase",
                        "brightness": 64,
                    },
                },
                {
                    "zoneId": "back",
                    "pattern": {
                        "colors": [16711680],
                        "animation": "motionless",
                        "brightness": 80,
                    },
                },
            ],
        },
    )
    await asyncio.sleep(5.05)
    coordinator.data = await coordinator._async_update_data()
    whole = GemstoneLight(coordinator, "hub")
    # When the whole design is dimmed and restored, then the front zone is brightened independently.
    await whole.async_turn_on(brightness=128)
    dimmed = deepcopy(vendor.states["hub"]["architectural"])
    await whole.async_turn_on(brightness=255)
    await GemstoneZoneLight(coordinator, "hub", "front").async_turn_on(brightness=200)
    # Then global brightness changes once, and the independent edit leaves the back at its original level.
    assert dimmed["brightness"] == 128
    assert dimmed["zonePatterns"][0]["pattern"]["colors"] == [64, 16384]
    final = vendor.states["hub"]["architectural"]["zonePatterns"]
    assert final[0]["pattern"]["colors"] == [200, 51200]
    assert final[1]["pattern"]["colors"] == [5242880]
    assert GemstoneZoneLight(coordinator, "hub", "back").brightness == 80
