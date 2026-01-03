from datetime import timedelta

DOMAIN = "hass_energy"

CONF_BASE_URL = "base_url"
CONF_TIMEOUT = "timeout"
DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 10
DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)
