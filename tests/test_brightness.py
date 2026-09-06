"""Cloud brightness must operate on logical RGBW, not already-dimmed channels."""

from custom_components.gemstone_lights.light import GemstoneLight


async def test_dim_then_brighten_restores_original_cloud_color(coordinator, vendor):
    # Given a full red controller in cloud-only mode.
    light = GemstoneLight(coordinator, "hub")
    # When it is dimmed, its physical echo is polled, and then brightness is restored.
    await light.async_turn_on(brightness=80)
    coordinator._pending_states.clear()
    coordinator.data = await coordinator._async_update_data()
    await light.async_turn_on(brightness=255)
    # Then the first cloud write is scaled red and the second restores the full original channel.
    assert [write[3] for write in vendor.writes] == [{"color": 80}, {"color": 255}]
    assert light.rgbw_color == (255, 0, 0, 0)
    assert light.brightness == 255


async def test_external_color_change_replaces_remembered_brightness(coordinator, vendor):
    # Given HA previously dimmed a red light.
    await coordinator.async_play_color("hub", 255, 80)
    coordinator._pending_states.clear()
    vendor.states["hub"] = {"onState": True, "color": 65280}
    # When a later poll observes a different green color set outside HA.
    coordinator.data = await coordinator._async_update_data()
    light = GemstoneLight(coordinator, "hub")
    # Then the old red/brightness interpretation is discarded instead of masking the external change.
    assert light.rgbw_color == (0, 255, 0, 0)
    assert light.brightness == 255
