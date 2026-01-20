from datetime import timedelta

DOMAIN = "hass_energy"

CONF_BASE_URL = "base_url"
CONF_TIMEOUT = "timeout"
DEFAULT_BASE_URL = "http://localhost:6070"
DEFAULT_TIMEOUT = 30
DEFAULT_SCAN_INTERVAL = timedelta(seconds=90)
