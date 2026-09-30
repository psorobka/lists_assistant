"""Integration constants and the public Listonic web client configuration."""

DOMAIN = "lists_assistant"
BASE_URL = "https://api.listonic.com"
CLIENT_ID = "listonicv2"
# Public application credential distributed in Listonic's web bundle, not a user token.
CLIENT_SECRET = "fjdfsoj9874jdfhjkh34jkhffdfff"
REDIRECT_URI = "https://listonicv2api.jestemkucharzem.pl"
DEFAULT_INTERVAL = 30
CONF_LISTS = "list_ids"
CONF_BRIDGE = "shopping_list_id"
CONF_DISCOVER = "discover_new"
CONF_INTERVAL = "scan_interval"
CONF_BRIDGE_CONFIRMED = "confirmed_shopping_list_id"
BRIDGE_OWNER = "listonic_shopping_list_owner"


def settings(entry):
    """Options override initial choices; token rotation stays in entry data."""
    return {**entry.data, **entry.options}
