"""Constants for the Gemstone Lights integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "gemstone_lights"

# --- Gemstone cloud (AWS) --------------------------------------------------
# These identifiers are embedded in the official Gemstone Lights mobile app.
AWS_REGION: Final = "us-west-2"
COGNITO_USER_POOL_ID: Final = "us-west-2_rr5lY7Etr"
COGNITO_CLIENT_ID: Final = "2647t144niotrl53vvru0ivno7"
API_BASE_URL: Final = "https://mytpybpq12.execute-api.us-west-2.amazonaws.com/prod"

# The API rejects requests that do not look like the mobile app.
APP_HEADERS: Final = {
    "app-environment": "Production",
    "app-platform": "Android",
    "app-version": "0.6.64",
    "app-build-number": "664",
    "app-device-type": "phone",
    "content-type": "application/json",
}

# --- Integration -----------------------------------------------------------
CONF_EMAIL: Final = "email"
CONF_PASSWORD: Final = "password"

DEFAULT_SCAN_INTERVAL: Final = timedelta(seconds=30)
# Saved designs/patterns change rarely; refresh them far less often.
CATALOG_REFRESH_INTERVAL: Final = timedelta(minutes=15)

REQUEST_TIMEOUT: Final = 30

MANUFACTURER: Final = "Gemstone Lights"

# Keys used inside the coordinator data structure.
DATA_DEVICES: Final = "devices"
DATA_INFO: Final = "info"
DATA_STATE: Final = "state"
DATA_DESIGNS: Final = "designs"
DATA_PATTERNS: Final = "patterns"

# Shown in a select when nothing recognisable is playing.
OPTION_NONE: Final = "None"
