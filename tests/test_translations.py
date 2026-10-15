"""Ensure native config flow labels and errors exist in both languages."""

import json
from pathlib import Path
from string import Formatter

import pytest

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "lists_assistant"


@pytest.mark.parametrize(
    "filename", ["strings.json", "translations/en.json", "translations/pl.json"]
)
def test_fixable_issue_uses_flow_instead_of_static_description(filename):
    """Hassfest treats description and fix_flow as mutually exclusive."""
    issues = json.loads((ROOT / filename).read_text())["issues"]
    for key, issue in issues.items():
        assert not ("fix_flow" in issue and "description" in issue), key


def flatten(value, prefix=""):
    result = {}
    for key, text in value.items():
        path = f"{prefix}.{key}"
        if isinstance(text, dict):
            result.update(flatten(text, path))
        else:
            result[path] = text
    return result


@pytest.mark.parametrize("language", ["en", "pl"])
def test_translation_keys_and_placeholders(language):
    source = flatten(json.loads((ROOT / "strings.json").read_text()))
    translated = flatten(
        json.loads((ROOT / "translations" / f"{language}.json").read_text())
    )
    assert source.keys() == translated.keys()
    for key, text in translated.items():
        assert isinstance(text, str) and text.strip(), key
        expected = {field for _, field, _, _ in Formatter().parse(source[key]) if field}
        actual = {field for _, field, _, _ in Formatter().parse(text) if field}
        assert actual == expected, key
