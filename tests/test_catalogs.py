"""Catalog refreshes must distinguish deletion from incomplete network results."""


async def test_successful_empty_catalog_removes_deleted_patterns(coordinator, vendor):
    # Given a previously saved pattern has been deleted from the account.
    coordinator._patterns = [{"name": "Deleted", "data": {"name": "Deleted"}}]
    vendor.folders = []
    # When a complete catalog refresh succeeds with no folders.
    await coordinator._async_refresh_catalog(["hub"])
    coordinator.data = await coordinator._async_update_data()
    # Then deleted patterns are removed from the selectable catalog.
    assert coordinator.patterns() == []


async def test_partial_folder_failure_retains_the_previous_complete_catalog(coordinator, vendor):
    # Given a complete old catalog and a refresh whose second folder cannot be read.
    previous = [{"name": "Previous", "data": {"name": "Previous"}}]
    coordinator._patterns = previous
    vendor.folders = [{"folderId": "first"}, {"folderId": "second"}]
    vendor.patterns["first"] = [{"patternData": {"name": "Partial replacement"}}]
    vendor.failures["/folders/pattern/list?folderId=second"] = 503
    # When the refresh receives only the first folder successfully.
    await coordinator._async_refresh_catalog(["hub"])
    # Then the existing complete catalog survives and a failed attempt is not marked fresh.
    assert coordinator._patterns == previous
    assert coordinator._catalog_refreshed is None
