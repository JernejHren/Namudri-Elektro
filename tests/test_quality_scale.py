"""quality_scale.yaml covers every rule and does not hide unfinished work."""

from __future__ import annotations

from pathlib import Path

import yaml

FILE = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "namudri_elektro"
    / "quality_scale.yaml"
)

BRONZE = {
    "action-setup", "appropriate-polling", "brands", "common-modules", "config-flow",
    "config-flow-test-coverage", "dependency-transparency", "docs-actions",
    "docs-conditions", "docs-high-level-description", "docs-installation-instructions",
    "docs-removal-instructions", "docs-triggers", "entity-event-setup",
    "entity-unique-id", "has-entity-name", "runtime-data", "test-before-configure",
    "test-before-setup", "unique-config-entry",
}
SILVER = {
    "action-exceptions", "config-entry-unloading", "docs-configuration-parameters",
    "docs-installation-parameters", "entity-unavailable", "integration-owner",
    "log-when-unavailable", "parallel-updates", "reauthentication-flow", "test-coverage",
}
GOLD = {
    "devices", "diagnostics", "discovery", "discovery-update-info", "docs-data-update",
    "docs-examples", "docs-known-limitations", "docs-supported-devices",
    "docs-supported-functions", "docs-troubleshooting", "docs-use-cases",
    "dynamic-devices", "entity-category", "entity-device-class",
    "entity-disabled-by-default", "entity-translations", "exception-translations",
    "icon-translations", "reconfiguration-flow", "repair-issues", "stale-devices",
}
PLATINUM = {"async-dependency", "inject-websession", "strict-typing"}


def _rules() -> dict:
    return yaml.safe_load(FILE.read_text())["rules"]


def _status(rule) -> str:
    return rule if isinstance(rule, str) else rule["status"]


def test_every_rule_of_every_tier_is_listed() -> None:
    assert set(_rules()) == BRONZE | SILVER | GOLD | PLATINUM


def test_statuses_are_valid_and_exemptions_are_explained() -> None:
    for name, rule in _rules().items():
        assert _status(rule) in {"done", "todo", "exempt"}, name
        if _status(rule) in {"exempt", "todo"}:
            assert isinstance(rule, dict) and rule.get("comment"), name


def test_silver_is_complete_and_bronze_has_only_the_known_gap() -> None:
    rules = _rules()
    assert {n for n in SILVER if _status(rules[n]) == "todo"} == set()
    assert {n for n in BRONZE if _status(rules[n]) == "todo"} == {"brands"}
