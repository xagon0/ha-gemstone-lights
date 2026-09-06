"""Design transport must match actual controller capabilities."""


async def test_zone_pattern_design_uses_cloud_even_with_local_available(
    coordinator, vendor
):
    # Given a reachable local controller and a design containing an animated zone.
    vendor.devices[0]["hub"] = {"localIp": "192.0.2.10", "tcpEnabled": True}
    coordinator.data = await coordinator._async_update_data()
    design = {
        "id": "saved",
        "zonePatterns": [
            {
                "zoneId": "front",
                "pattern": {"animation": "chase", "colors": [255, 65280]},
            }
        ],
    }
    # When the saved design is selected.
    await coordinator.async_play_design("hub", design)
    # Then the unsupported zonePatterns payload goes only to the cloud with preview disabled.
    assert vendor.writes == [
        (
            "cloud",
            "hub",
            "/deviceControl/play/architectural",
            {
                "architectural": {
                    **design,
                    "preview": False,
                    "zonePatterns": [
                        {
                            "zoneId": "front",
                            "pattern": {
                                "animation": "chase",
                                "colors": [255, 65280],
                                "brightness": 255,
                            },
                        }
                    ],
                }
            },
        )
    ]


async def test_static_pixel_design_uses_local_transport(coordinator, vendor):
    # Given a reachable local controller and a supported explicit-pixel design.
    vendor.devices[0]["hub"] = {"localIp": "192.0.2.10", "tcpEnabled": True}
    coordinator.data = await coordinator._async_update_data()
    design = {
        "staticColors": [{"lights": [10, 11, 12], "color": 255}],
        "brightness": 80,
    }
    # When the pixel design is played.
    await coordinator.async_play_design("hub", design)
    # Then it is sent over LAN with its brightness and pixel assignments intact.
    assert vendor.writes[-1][0] == "local"
    assert vendor.writes[-1][3]["architectural"] == {**design, "preview": False}
