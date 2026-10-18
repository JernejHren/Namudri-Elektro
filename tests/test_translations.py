"""The translation files, services.yaml and icons.json agree with each other."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "namudri_elektro"


def _load(name: str) -> dict:
    return json.loads((COMPONENT / name).read_text(encoding="utf-8"))


def _paths(data, prefix: str = "") -> set[str]:
    """All leaf key paths of a nested dict."""
    if not isinstance(data, dict):
        return {prefix}
    result: set[str] = set()
    for key, value in data.items():
        result |= _paths(value, f"{prefix}.{key}" if prefix else key)
    return result


def test_english_translation_is_a_copy_of_strings_json() -> None:
    assert _load("translations/en.json") == _load("strings.json")


def test_slovenian_has_the_same_keys_as_english() -> None:
    english = _paths(_load("strings.json"))
    slovenian = _paths(_load("translations/sl.json"))
    assert english - slovenian == set(), "missing in sl.json"
    assert slovenian - english == set(), "unknown keys in sl.json"


def test_every_service_is_described() -> None:
    services = yaml.safe_load((COMPONENT / "services.yaml").read_text())
    strings = _load("strings.json")
    assert set(services) == set(strings["services"])
    for name, definition in services.items():
        assert set(definition["fields"]) == set(strings["services"][name]["fields"])


def test_every_entity_has_a_name_and_an_icon() -> None:
    strings = _load("strings.json")["entity"]
    icons = _load("icons.json")["entity"]
    assert set(strings) == set(icons)
    for platform, entities in strings.items():
        assert set(entities) == set(icons[platform]), platform


@pytest.mark.parametrize("language", ["strings.json", "translations/sl.json"])
def test_exceptions_used_in_code_are_translated(language: str) -> None:
    used = {
        "source_entity_not_found",
        "entry_not_found",
        "no_loaded_entry",
        "shedding_disabled",
        "nothing_to_shed",
    }
    assert used <= set(_load(language)["exceptions"])


def test_config_flow_fields_are_described() -> None:
    strings = _load("strings.json")
    for section in ("config", "options"):
        for step_id, step in strings[section]["step"].items():
            assert set(step["data"]) == set(step["data_description"]), (section, step_id)
