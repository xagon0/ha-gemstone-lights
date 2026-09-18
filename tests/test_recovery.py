"""Cloud recovery runs through real coordinator, API, service and storage logic."""

import asyncio
from datetime import timedelta

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.gemstone_lights.coordinator import GemstoneCoordinator
from custom_components.gemstone_lights.recovery import RecoveryPolicy


def reboots(vendor):
    return [w for w in vendor.writes if w[2] == "/deviceControl/softReboot"]


async def failed_probe(coordinator, vendor, freezer, minutes=1):
    vendor.failures["/device-state/currently-playing"] = 500
    freezer.tick(timedelta(minutes=minutes))
    await coordinator.async_refresh()


async def test_manual_reboot_available_during_lan_outage(
    hass, loaded_entry, vendor, http
):
    # Given local reads fail but the account still reports the controller online.
    vendor.failures["/device-state/currently-playing"] = 500
    vendor.failures["/deviceControl/currentlyPlaying"] = 500
    await loaded_entry.async_refresh()
    assert hass.states.get("light.house").state == "unavailable"
    vendor.failures.pop("/deviceControl/currentlyPlaying")
    before = vendor.states["hub"].copy()
    # When an administrator calls the controller action through HA.
    await hass.services.async_call(
        "gemstone_lights", "soft_reboot", {"controller": "light.house"}, blocking=True
    )
    # Then the captured app POST format is used without replacing the lighting content.
    assert reboots(vendor) == [
        (
            "cloud",
            "hub",
            "/deviceControl/softReboot",
            {"deviceId": "hub", "homegroupId": "home"},
        )
    ]
    requests = [
        (method, calls)
        for (method, url), calls in http.requests.items()
        if url.path.endswith("/softReboot")
    ]
    assert requests[0][0] == "POST"
    assert requests[0][1][0].kwargs["json"] is None
    assert vendor.states["hub"] == before
    assert loaded_entry.recovery.history["hub"]["attempted_outage"]


async def test_concurrent_manual_clicks_only_send_one_reboot(
    hass, loaded_entry, vendor
):
    # Given a cloud-capable controller and simultaneous reboot requests.
    # When both requests compete for the controller lock.
    results = await asyncio.gather(
        loaded_entry.async_soft_reboot("hub"),
        loaded_entry.async_soft_reboot("hub"),
        return_exceptions=True,
    )
    # Then exactly one request reaches the device and the duplicate reports its cooldown.
    assert len(reboots(vendor)) == 1
    assert sum(isinstance(r, HomeAssistantError) for r in results) == 1
    assert "two minutes" in str(next(r for r in results if isinstance(r, Exception)))


async def test_automatic_recovery_waits_for_sustained_failure(
    loaded_entry, vendor, freezer
):
    # Given opted-in recovery with a ten-minute threshold.
    loaded_entry._auto_recovery = True
    await failed_probe(loaded_entry, vendor, freezer, 0)
    # When eleven actual failed LAN probes span ten minutes.
    for _ in range(9):
        await failed_probe(loaded_entry, vendor, freezer)
    assert not reboots(vendor)
    await failed_probe(loaded_entry, vendor, freezer)
    # Then exactly one reboot is requested while the cloud-backed light remains available.
    assert len(reboots(vendor)) == 1
    assert loaded_entry.device_available("hub")
    for _ in range(3):
        await failed_probe(loaded_entry, vendor, freezer, 10)
    assert len(reboots(vendor)) == 1


@pytest.mark.parametrize(
    "guard",
    [
        "disabled",
        "cloud_down",
        "offline",
        "tcp_disabled",
        "wrong_ip",
        "missing_membership",
        "cloud_state_failure",
    ],
)
async def test_automatic_reboot_requires_all_preconditions(
    loaded_entry, vendor, freezer, guard
):
    # Given enough failed probes, but one prerequisite is absent.
    loaded_entry._auto_recovery = guard != "disabled"
    for _ in range(3):
        await failed_probe(loaded_entry, vendor, freezer, 1)
    if guard == "cloud_down":
        vendor.cloud_offline = True
    elif guard == "offline":
        vendor.devices[0]["online"] = False
    elif guard == "tcp_disabled":
        vendor.devices[0]["hub"]["tcpEnabled"] = False
    elif guard == "wrong_ip":
        loaded_entry._host_override = "192.0.2.10"
        loaded_entry._host_device_id = "hub"
        vendor.devices[0]["hub"]["localIp"] = "192.0.2.11"
    elif guard == "missing_membership":
        vendor.devices = []
    elif guard == "cloud_state_failure":
        vendor.failures["/deviceControl/currentlyPlaying"] = 500
    # When enough time has elapsed for recovery.
    await failed_probe(loaded_entry, vendor, freezer, 20)
    # Then no reboot is sent under the unsafe or disabled condition.
    assert not reboots(vendor)
    assert not loaded_entry.recovery.history.get("hub", {}).get("attempted_outage")


async def test_local_only_blocks_manual_and_automatic_reboot(
    loaded_entry, vendor, http, freezer
):
    # Given strict local-only mode even if the saved auto-recovery preference is enabled.
    loaded_entry.api = None
    loaded_entry._auto_recovery = True
    http.requests.clear()
    # When LAN stays down and an administrator requests reboot.
    for _ in range(3):
        await failed_probe(loaded_entry, vendor, freezer, 10)
    with pytest.raises(HomeAssistantError, match="local-only"):
        await loaded_entry.async_soft_reboot("hub")
    # Then no cloud endpoint is contacted and no reboot is issued.
    assert not reboots(vendor)
    assert all(url.host == "192.0.2.10" for _, url in http.requests)


async def test_failed_reboot_response_is_not_retried_after_reload(
    hass, entry, loaded_entry, vendor, freezer, api, http
):
    # Given a sustained outage whose reboot endpoint returns an ambiguous server failure.
    loaded_entry._auto_recovery = True
    vendor.failures["/deviceControl/softReboot"] = 500
    for _ in range(3):
        await failed_probe(loaded_entry, vendor, freezer, 6)
    assert (
        sum(
            len(v)
            for (method, url), v in http.requests.items()
            if url.path.endswith("/softReboot")
        )
        == 1
    )
    # When a new coordinator restores HA's saved history and the outage continues beyond the cooldown.
    restored = GemstoneCoordinator(
        hass, entry, api, auto_recovery=True, enable_library=False
    )
    await restored._async_setup()
    for _ in range(3):
        await failed_probe(restored, vendor, freezer, 400)
    # Then the attempted outage survives restart and no second reboot is sent.
    assert restored.recovery.history["hub"]["attempted_outage"]
    assert (
        sum(
            len(v)
            for (method, url), v in http.requests.items()
            if url.path.endswith("/softReboot")
        )
        == 1
    )
    await restored.async_shutdown()


async def test_successful_lan_read_rearms_outage_but_preserves_cooldown(
    loaded_entry, vendor, freezer
):
    # Given automatic recovery has already requested one reboot.
    loaded_entry._auto_recovery = True
    for _ in range(3):
        await failed_probe(loaded_entry, vendor, freezer, 6)
    assert len(reboots(vendor)) == 1
    # When LAN recovers, then fails again inside the six-hour cooldown.
    vendor.failures.clear()
    freezer.tick(timedelta(minutes=2))
    await loaded_entry.async_refresh()
    assert loaded_entry.control_transport("hub") == "local"
    for _ in range(3):
        await failed_probe(loaded_entry, vendor, freezer, 6)
    assert len(reboots(vendor)) == 1
    freezer.tick(timedelta(hours=6))
    await loaded_entry.async_refresh()
    # Then the new outage can consume its one attempt only after the cooldown expires.
    assert len(reboots(vendor)) == 2


async def test_healthy_local_control_never_reboots(loaded_entry, vendor, freezer):
    # Given recovery is enabled and LAN is healthy.
    loaded_entry._auto_recovery = True
    # When normal state polling continues across several hours.
    for _ in range(3):
        freezer.tick(timedelta(hours=8))
        await loaded_entry.async_refresh()
    # Then local operation remains available without any reboot requests.
    assert loaded_entry.control_transport("hub") == "local"
    assert not reboots(vendor)


def test_single_failure_and_changed_address_do_not_meet_recovery_threshold():
    # Given one failed probe followed by a long time without further probes.
    policy = RecoveryPolicy()
    policy.failed("hub", "old-host", 1000)
    # When additional failures concern a different controller address.
    assert not policy.due("hub", 10000, 600, 21600)
    policy.failed("hub", "new-host", 10000)
    policy.failed("hub", "new-host", 10060)
    # Then the old address's failure does not contribute to recovery eligibility.
    assert not policy.due("hub", 20000, 600, 21600)


async def test_reboot_auth_rejection_is_not_replayed(api, http):
    # Given the reboot API rejects the currently valid access token.
    from custom_components.gemstone_lights.api import GemstoneAuthError
    from custom_components.gemstone_lights.const import API_BASE_URL

    http.post(
        f"{API_BASE_URL}/deviceControl/softReboot?deviceId=hub&homegroupId=home",
        status=401,
        repeat=True,
    )
    # When a soft reboot is requested.
    with pytest.raises(GemstoneAuthError):
        await api.async_soft_reboot("hub", "home")
    # Then only one HTTP attempt occurs; the non-idempotent request is not retried.
    assert sum(len(calls) for calls in http.requests.values()) == 1


async def test_failed_cooldown_storage_prevents_reboot(
    loaded_entry, vendor, monkeypatch
):
    # Given HA storage cannot persist the attempt reservation.
    from homeassistant.helpers.storage import Store

    async def failed_save(store, data):
        raise OSError("Disk unavailable")

    monkeypatch.setattr(Store, "async_save", failed_save)
    # When the administrator requests a reboot.
    with pytest.raises(HomeAssistantError, match="no reboot sent"):
        await loaded_entry.async_soft_reboot("hub")
    # Then the controller receives no reboot that could repeat after an HA restart.
    assert not reboots(vendor)


async def test_recovery_options_survive_reload_and_local_only_wins(
    hass, entry, loaded_entry
):
    # Given an account integration with cloud available.
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    # When recovery is configured while strict local-only mode is selected.
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {
            "auto_recovery": True,
            "local_only": True,
            "recovery_delay_minutes": 15,
            "recovery_cooldown_hours": 12,
        },
    )
    await hass.async_block_till_done()
    # Then the preference is retained, but cloud and automatic recovery are disabled at runtime.
    assert result["type"] == "create_entry"
    assert entry.options["auto_recovery"] is True
    assert entry.runtime_data.api is None
    assert entry.runtime_data._auto_recovery is False
    assert entry.runtime_data._recovery_delay == 900
    assert entry.runtime_data._recovery_cooldown == 43200
