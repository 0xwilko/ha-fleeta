"""Constants for the FLEETA (BlackVue) integration."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Final

DOMAIN: Final = "fleeta"
LOGGER = logging.getLogger(__package__)

API_BASE_URL: Final = "https://openapi.fleeta.io"
API_TIMEOUT_SECONDS: Final = 30
WEB_VIEWER_URL: Final = "https://app.fleeta.io/"

# The FLEETA Car dashboard card, served by this integration.
CARD_URL: Final = f"/{DOMAIN}/fleeta-card.js"

MANUFACTURER: Final = "BlackVue"

# Option: fixed polling interval in minutes. 0 means "automatic" (paced to the
# monthly API call allowance reported by FLEETA).
CONF_SCAN_INTERVAL_MINUTES: Final = "scan_interval_minutes"
DEFAULT_SCAN_INTERVAL_MINUTES: Final = 0

# Calls made on every polling cycle: fleet locations + events feed.
CALLS_PER_CYCLE: Final = 2

# Slower refreshes for data that rarely changes.
DEVICES_REFRESH: Final = timedelta(hours=3)
USAGE_REFRESH: Final = timedelta(hours=6)
# The last trip is read when a drive ends, and at least this often.
TRIPS_REFRESH: Final = timedelta(hours=6)

# Automatic pacing limits.
MIN_INTERVAL: Final = timedelta(minutes=2)
MAX_INTERVAL: Final = timedelta(minutes=60)
LIMIT_REACHED_INTERVAL: Final = timedelta(hours=1)
# Only plan to use this share of the remaining monthly allowance, leaving room
# for the AI assistant (MCP) connection and the web viewer's own API use.
BUDGET_SHARE: Final = 0.85
# Poll faster while the car is moving and slower while it is parked. Pacing is
# recalculated every cycle from the calls actually left, so it self-corrects.
MOVING_FACTOR: Final = 0.5
PARKED_FACTOR: Final = 1.5
# Fallback when the allowance is not known yet.
FALLBACK_INTERVAL: Final = timedelta(minutes=10)

STATUS_DRIVING: Final = "driving"
STATUS_PARKED: Final = "parked"
STATUS_OFFLINE: Final = "offline"
STATUSES: Final = [STATUS_DRIVING, STATUS_PARKED, STATUS_OFFLINE]

# Event types reported by FLEETA (GET /v1/events). Anything new is reported as
# "other" with the raw type kept in the event attributes.
EVENT_OTHER: Final = "other"
EVENT_TYPES: Final = [
    "parking_impact",
    "driving_impact",
    "harsh_braking",
    "harsh_acceleration",
    "sharp_turn",
    "overspeed",
    "speed_limit",
    "manual",
    "drowsy",
    "distracted",
    "undetected",
    "calling",
    "smoking",
    "seatbelt",
    "geofence_enter",
    "geofence_exit",
    "geofence_pass",
    "geofence_speed",
    EVENT_OTHER,
]

SIGNAL_NEW_EVENT: Final = f"{DOMAIN}_new_event_{{entry_id}}"
