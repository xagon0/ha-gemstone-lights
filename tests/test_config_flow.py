"""Account setup and reauthentication through Home Assistant's real flow manager."""

from botocore.exceptions import ClientError
from homeassistant.helpers import entity_registry as er


async def test_duplicate_account_is_rejected_before_login(
    hass, entry, http, enable_custom_integrations
):
    # Given the account already exists under its normalized email address.
    # When the same account is submitted with different case and surrounding spaces.
    result = await hass.config_entries.flow.async_init(
        "gemstone_lights",
        context={"source": "user"},
        data={"email": "  TEST@example.invalid  ", "password": "unused"},
    )
    # Then it is rejected as a duplicate without contacting either authentication or the cloud API.
    assert result["reason"] == "already_configured"
    assert http.requests == {}


async def test_bad_credentials_return_an_actionable_setup_error(
    hass, vendor, cognito_external, monkeypatch, enable_custom_integrations
):
    # Given Cognito rejects an account's credentials at the external SDK boundary.
    def reject(user, password):
        raise ClientError({"Error": {"Code": "NotAuthorizedException"}}, "InitiateAuth")

    monkeypatch.setattr("pycognito.Cognito.authenticate", reject)
    # When the user submits the setup form.
    result = await hass.config_entries.flow.async_init(
        "gemstone_lights",
        context={"source": "user"},
        data={"email": "new@example.invalid", "password": "incorrect"},
    )
    # Then setup stays on the form with an authentication error and creates no config entry.
    assert result["errors"] == {"base": "invalid_auth"}
    assert hass.config_entries.async_entries("gemstone_lights") == []


async def test_reauthentication_reloads_without_duplicate_entities(
    hass, entry, loaded_entry, vendor
):
    # Given a loaded account with existing entity registry entries.
    before = {entity.entity_id for entity in er.async_get(hass).entities.values()}
    flow = await hass.config_entries.flow.async_init(
        "gemstone_lights",
        context={"source": "reauth", "entry_id": entry.entry_id},
        data=entry.data,
    )
    # When the user supplies a new password that the external SDK accepts.
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"password": "new-test-password"}
    )
    await hass.async_block_till_done()
    # Then credentials update, the coordinator is recreated, and entity identities are preserved.
    assert result["reason"] == "reauth_successful"
    assert entry.data["password"] == "new-test-password"
    assert entry.runtime_data is not loaded_entry
    assert {
        entity.entity_id for entity in er.async_get(hass).entities.values()
    } == before
