"""Authentication tests intercept Cognito's external AWS requests only."""

from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from custom_components.gemstone_lights.api import GemstoneApiError, GemstoneAuthError


@pytest.fixture(autouse=True)
def aws_environment(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")


@pytest.mark.parametrize("failure", [
    EndpointConnectionError(endpoint_url="https://cognito-idp.us-west-2.amazonaws.com"),
    ClientError({"Error": {"Code": "TooManyRequestsException"}}, "InitiateAuth"),
])
async def test_transient_login_failure_allows_retry(api, failure):
    # Given AWS is unreachable or temporarily rate limiting authentication.
    with patch("botocore.client.BaseClient._make_api_call", side_effect=failure):
        # When the integration attempts a real SRP login.
        with pytest.raises(GemstoneApiError) as raised:
            await api.async_login()
    # Then the failure is retryable, rather than prompting for a new password.
    assert raised.value.__cause__ is failure


async def test_rejected_credentials_request_reauthentication(api):
    # Given Cognito explicitly rejects the supplied credentials.
    failure = ClientError({"Error": {"Code": "NotAuthorizedException"}}, "InitiateAuth")
    with patch("botocore.client.BaseClient._make_api_call", side_effect=failure):
        # When the integration authenticates.
        with pytest.raises(GemstoneAuthError, match="credentials"):
            await api.async_login()
    # Then no usable session is established by the failed login.
    # The pre-existing token is retained; login failure must not destroy it.
    assert api._access_token is not None
