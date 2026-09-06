"""Authentication tests intercept Cognito's external AWS requests only."""

import asyncio
import time
from unittest.mock import patch

import jwt
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from custom_components.gemstone_lights.api import GemstoneApiError, GemstoneAuthError
from custom_components.gemstone_lights.const import API_BASE_URL


@pytest.fixture(autouse=True)
def aws_environment(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")


@pytest.mark.parametrize(
    "failure",
    [
        EndpointConnectionError(
            endpoint_url="https://cognito-idp.us-west-2.amazonaws.com"
        ),
        ClientError({"Error": {"Code": "TooManyRequestsException"}}, "InitiateAuth"),
    ],
)
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


async def test_near_expiry_session_is_renewed_once_for_concurrent_requests(api, http):
    # Given an unexpired session inside the refresh window and AWS returning a new one.
    api._store(
        {
            "access_token": jwt.encode({"exp": time.time() + 30}, "test-key" * 8),
            "refresh_token": "refresh",
        }
    )
    renewed = jwt.encode({"exp": time.time() + 3600}, "test-key" * 8)

    def renew(user):
        user.access_token = renewed
        user.id_token = renewed

    http.get(f"{API_BASE_URL}/homegroup/list", payload={"data": []}, repeat=True)
    with patch(
        "pycognito.Cognito.renew_access_token", autospec=True, side_effect=renew
    ) as aws:
        # When two requests need a token at the same time.
        await asyncio.gather(api.async_get_homegroups(), api.async_get_homegroups())
    # Then both HTTP requests use the renewed session and only one AWS renewal occurs.
    assert aws.call_count == 1
    assert [
        call.kwargs["headers"]["authorization"]
        for calls in http.requests.values()
        for call in calls
    ] == [f"Bearer {renewed}"] * 2


async def test_repeated_http_auth_rejection_stops_after_one_renewal(api, http):
    # Given the cloud rejects both the current and renewed session.
    http.get(f"{API_BASE_URL}/homegroup/list", status=401, repeat=True)
    renewed = jwt.encode({"exp": time.time() + 3600}, "test-key" * 8)

    def renew(user):
        user.access_token = renewed
        user.id_token = renewed

    with patch(
        "pycognito.Cognito.renew_access_token", autospec=True, side_effect=renew
    ):
        # When a request exhausts its single authentication retry.
        with pytest.raises(GemstoneAuthError, match="renewed session"):
            await api.async_get_homegroups()
    # Then it stops after two HTTP attempts instead of looping or reporting an ordinary outage.
    assert sum(map(len, http.requests.values())) == 2
