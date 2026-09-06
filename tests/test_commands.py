"""Commands must publish successful state even while the cloud echo lags."""

from custom_components.gemstone_lights.light import GemstoneLight


async def test_successful_color_is_immediate_and_survives_stale_poll(coordinator, vendor):
    # Given the cloud accepts writes but reports its previous state for a while.
    vendor.echo_writes = False
    light = GemstoneLight(coordinator, "hub")
    # When a dim white-channel color is sent, followed by a stale poll.
    await coordinator.async_play_color("hub", 4278190080, 80)
    coordinator.data = await coordinator._async_update_data()
    # Then the emitted color is scaled for cloud while HA retains the requested RGBW and brightness.
    assert vendor.writes[-1][3] == {"color": 1342177280}
    assert light.rgbw_color == (0, 0, 0, 255)
    assert light.brightness == 80
