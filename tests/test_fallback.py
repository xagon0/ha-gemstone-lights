"""A failed local command must retry through cloud without corrupting its payload."""


async def test_failed_local_color_write_falls_back_to_scaled_cloud_rgbw(
    coordinator, vendor
):
    # Given a controller was reachable locally but now rejects local writes.
    vendor.devices[0]["hub"] = {"localIp": "192.0.2.10", "tcpEnabled": True}
    coordinator.data = await coordinator._async_update_data()
    vendor.failures["/device-control/play"] = 503
    # When a dimmed white-channel color is requested.
    await coordinator.async_play_color("hub", 4278190080, 80)
    # Then cloud receives the correctly scaled payload and the failed LAN route enters backoff.
    assert vendor.writes[-1] == (
        "cloud",
        "hub",
        "/deviceControl/play/color",
        {"color": 1342177280},
    )
    assert not coordinator.is_local("hub")
