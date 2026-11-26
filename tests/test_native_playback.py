"""Native playlists can expose different playback details on LAN and cloud."""

from copy import deepcopy
from datetime import timedelta

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.gemstone_lights.playback import playlist_step

SENSOR = "sensor.house_now_playing"
TIMER = {
    "onState": True,
    "timerId": "timer-1",
    "timer": {"id": "timer-1"},
    "color": 0xFFFFFFFF,
}


def demo(start):
    return {
        "name": "Demo Playlist",
        "sequence": "ordered",
        "startedAt": start,
        "playlistData": [
            {"index": 1, "lengthInSeconds": 12, "pattern": {"name": "Pacman"}},
            {"index": 0, "lengthInSeconds": 30, "pattern": {"name": "Multipulse"}},
        ],
    }


async def test_local_timer_enriched_without_changing_local_controls(
    hass, loaded_entry, vendor, freezer
):
    # Given different LAN and account responses for the same physical controller.
    now = dt_util.utcnow().replace(microsecond=0)
    freezer.move_to(now)
    vendor.local_states["hub"] = deepcopy(TIMER)
    vendor.states["hub"] = {
        "onState": True,
        "playlist": demo(int(now.timestamp()) - 28),
    }
    # When polling the timer and then crossing the next item boundary between polls.
    await loaded_entry.async_refresh()
    initial = hass.states.get(SENSOR)
    later = now + timedelta(seconds=2)
    freezer.move_to(later)
    async_fire_time_changed(hass, later)
    await hass.async_block_till_done()
    # Then HA advances the item while the raw LAN state and control transport remain intact.
    assert initial.state == "Multipulse"
    assert initial.attributes["playlist"] == "Demo Playlist"
    assert hass.states.get(SENSOR).state == "Pacman"
    assert hass.states.get(SENSOR).attributes["pattern"] == "Pacman"
    assert hass.states.get(SENSOR).attributes["control"] == "local"
    assert loaded_entry.device_state("hub") == TIMER
    # When the cloud read fails while the local timer is still active.
    vendor.failures["/deviceControl/currentlyPlaying"] = 503
    await loaded_entry.async_refresh()
    # Then local control stays available and the sensor clears the previous item.
    fallback = hass.states.get(SENSOR)
    assert fallback.state == "Timer active"
    assert fallback.attributes["playlist"] is None
    assert fallback.attributes["pattern"] is None
    assert fallback.attributes["color"] == 0xFFFFFFFF


async def test_ha_solid_white_to_native_playlist(hass, loaded_entry, vendor, freezer):
    # Given a controller showing solid white from the Home Assistant light service.
    await hass.services.async_call(
        "light",
        "turn_on",
        {
            "entity_id": "light.house",
            "rgbw_color": [255, 255, 255, 255],
            "brightness": 255,
        },
        blocking=True,
    )
    assert hass.states.get(SENSOR).state == "#FFFFFF+W255"

    # When the app starts a playlist after the HA command has settled.
    freezer.tick(timedelta(seconds=6))
    now = dt_util.utcnow()
    vendor.local_states["hub"] = deepcopy(TIMER)
    vendor.states["hub"] = {"onState": True, "playlist": demo(int(now.timestamp()) - 1)}
    await loaded_entry.async_refresh()

    # Then the first refresh reports the current playlist item over local control.
    sensor = hass.states.get(SENSOR)
    assert sensor.state == "Multipulse"
    assert sensor.attributes["pattern"] == "Multipulse"
    assert sensor.attributes["playlist"] == "Demo Playlist"
    assert sensor.attributes["control"] == "local"


async def test_playlist_to_solid_white(hass, loaded_entry, vendor):
    # Given native playlist metadata provided directly by the controller transport.
    start = int(dt_util.utcnow().timestamp()) - 1
    vendor.states["hub"] = {"onState": True, "playlist": demo(start)}
    await loaded_entry.async_refresh()
    assert hass.states.get(SENSOR).state == "Multipulse"
    # When playback changes to a plain white color.
    vendor.states["hub"] = {"onState": True, "color": 0xFFFFFFFF}
    await loaded_entry.async_refresh()
    # Then the sensor reports real solid white and clears the playlist.
    sensor = hass.states.get(SENSOR)
    assert sensor.state == "#FFFFFF+W255"
    assert sensor.attributes["playlist"] is None
    assert sensor.attributes["pattern"] is None


async def test_account_state_falls_back_to_playlist_name_for_unknown_order(
    hass, loaded_entry, vendor
):
    # Given an unreachable LAN controller and account playback with unknown ordering.
    vendor.failures["/device-state/currently-playing"] = 503
    playlist = demo(int(dt_util.utcnow().timestamp()) - 1)
    playlist["sequence"] = "unrecognized"
    vendor.states["hub"] = {"onState": True, "playlist": playlist}
    # When the coordinator falls back to the account endpoint.
    await loaded_entry.async_refresh()
    # Then the sensor identifies the playlist and leaves the current item unspecified.
    sensor = hass.states.get(SENSOR)
    assert sensor.state == "Demo Playlist"
    assert sensor.attributes["pattern"] is None
    assert sensor.attributes["control"] == "cloud"


async def test_local_only_timer(hass, vendor, http, enable_custom_integrations):
    # Given the timer-and-color response from a local-only Hub2.
    vendor.local_states["hub"] = deepcopy(TIMER)
    flow = await hass.config_entries.flow.async_init(
        "gemstone_lights", context={"source": "user"}
    )
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"next_step_id": "local"}
    )
    # When the user sets up local control without a Gemstone account.
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"host": "192.0.2.10", "name": "House"}
    )
    await hass.async_block_till_done()
    # Then HA shows the timer and retains the raw color without making a cloud request.
    try:
        sensor = hass.states.get(SENSOR)
        assert sensor.state == "Timer active"
        assert sensor.attributes["color"] == 0xFFFFFFFF
        assert sensor.attributes["playlist"] is None
        assert {url.host for _, url in http.requests} == {"192.0.2.10"}
    finally:
        await hass.config_entries.async_unload(result["result"].entry_id)


@pytest.mark.parametrize(
    ("elapsed", "name", "remaining"),
    [
        (0, "Multipulse", 30),
        (29.5, "Multipulse", 0.5),
        (30, "Pacman", 12),
        (41, "Pacman", 1),
        (42, "Multipulse", 30),
        (84, "Multipulse", 30),
    ],
)
def test_ordered_playlist_boundaries(elapsed, name, remaining):
    # Given variable-duration steps with explicit indices in unsorted input.
    playlist = demo(100)
    # When reading the current item relative to the native start time.
    result = playlist_step(playlist, 100 + elapsed)
    # Then boundaries and wraps match the playlist's order and durations.
    assert result == (name, remaining)


@pytest.mark.parametrize(
    "change",
    [
        {"sequence": "unrecognized"},
        {"startedAt": 120},
        {"startedAt": None},
        {"playlistData": []},
        {"playlistData": [{"index": 0, "lengthInSeconds": 0}]},
        {"playlistData": [{"index": 0, "lengthInSeconds": 3}] * 2},
    ],
)
def test_unverified_timeline_has_no_current_item(change):
    # Given unknown ordering, an unstarted playlist, or incomplete timing.
    playlist = {**demo(100), **change}
    # When reading at a known time.
    result = playlist_step(playlist, 105)
    # Then the integration cannot identify an item or schedule a transition.
    assert result == (None, None)
