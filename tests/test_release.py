"""Check the installable integration and release metadata stay consistent."""

import json
import tomllib
from pathlib import Path

import yaml

from custom_components.lists_assistant.const import DOMAIN

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / DOMAIN


def test_release_metadata():
    manifest = json.loads((INTEGRATION / "manifest.json").read_text())
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text())["package"][0]
    hacs = json.loads((ROOT / "hacs.json").read_text())

    assert manifest["domain"] == DOMAIN == "lists_assistant"
    assert manifest["version"] == project["version"] == lock["version"]
    assert manifest["name"] == hacs["name"] == "Lists Assistant"
    assert manifest["codeowners"] == ["@psorobka"]
    assert manifest["documentation"] == "https://github.com/psorobka/lists_assistant"
    assert manifest["issue_tracker"] == manifest["documentation"] + "/issues"
    assert (INTEGRATION / "brand" / "icon.png").is_file()
    assert not (ROOT / "custom_components" / "listonic").exists()


def test_actions_select_the_renamed_integration():
    services = yaml.safe_load((INTEGRATION / "services.yaml").read_text())
    for service in services.values():
        selector = service["fields"]["config_entry_id"]["selector"]
        assert selector["config_entry"]["integration"] == DOMAIN
    choices = services["resolve_sync_conflict"]["fields"]["choose"]["selector"]
    assert "listonic" in choices["select"]["options"]
