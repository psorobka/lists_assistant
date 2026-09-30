"""Let recorder fixtures initialize before HA; integration opt-in is explicit."""

import pytest


@pytest.fixture(autouse=True)
def custom_integrations():
    """Override parent's eager HA setup; E2E requests enable_custom_integrations."""
