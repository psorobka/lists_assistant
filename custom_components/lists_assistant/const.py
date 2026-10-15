"""Integration constants and the public Listonic web client configuration."""

DOMAIN = "lists_assistant"
BASE_URL = "https://api.listonic.com"
CLIENT_ID = "listonicv2"
# Public application credential distributed in Listonic's web bundle, not a user token.
CLIENT_SECRET = "fjdfsoj9874jdfhjkh34jkhffdfff"
REDIRECT_URI = "https://listonicv2api.jestemkucharzem.pl"
DEFAULT_INTERVAL = 1
CONF_LISTS = "list_ids"
CONF_BRIDGE = "shopping_list_id"
CONF_DISCOVER = "discover_new"
CONF_INTERVAL = "scan_interval"
CONF_BRIDGE_CONFIRMED = "confirmed_shopping_list_id"
CONF_KEEP_ENABLED = "google_keep_enabled"
CONF_KEEP_USERNAME = "google_keep_username"
CONF_KEEP_TOKEN = "google_keep_token"
CONF_KEEP_ANDROID_ID = "google_keep_android_id"
CONF_KEEP_OAUTH_TOKEN = "google_keep_oauth_token"
CONF_KEEP_LIST_ID = "google_keep_list_id"
CONF_KEEP_LISTS = "google_keep_list_ids"
CONF_SOURCES = "sources"
CONF_CONFLICT_POLICY = "conflict_policy"
SOURCE_LISTONIC = "listonic"
SOURCE_KEEP = "google_keep"
BRIDGE_OWNER = "listonic_shopping_list_owner"


def settings(entry):
    """Options override initial choices; token rotation stays in entry data."""
    return {**entry.data, **entry.options}


def integration_title(sources):
    """Return a readable config-entry title for the connected providers."""
    providers = []
    if SOURCE_LISTONIC in sources:
        providers.append("Listonic")
    if SOURCE_KEEP in sources:
        providers.append("Google Keep")
    if not providers:
        providers.append("Listonic")
    return f"Lists Assistant ({' + '.join(providers)})"
