"""A successful HTTP response must eventually be checked against hardware."""

import asyncio

from homeassistant.helpers import entity_registry as er


async def test_post_command_refresh_observes_controller_after_settling(
    hass, loaded_entry, vendor
):
    # Given a red controller that accepts HTTP writes but does not apply the next command.
    vendor.echo_writes = False
    entity_id = er.async_get(hass).async_get_entity_id(
        "light", "gemstone_lights", "hub_light"
    )
    # When HA commands blue and its scheduled post-command refresh has time to run.
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": entity_id, "rgbw_color": [0, 0, 255, 0]},
        blocking=True,
    )
    await asyncio.sleep(5.5)
    await hass.async_block_till_done()
    # Then HA reflects the actual red output without waiting for the regular polling interval.
    assert hass.states.get(entity_id).attributes["rgbw_color"] == (255, 0, 0, 0)
