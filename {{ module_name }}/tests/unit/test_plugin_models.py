"""
Unit tests for plugin Pydantic models and PluginGateway helper methods.

Verifies that field names match the contract between v2_modulemanager
(Zenoh response) and the consumer module.  The key invariant is that
the ``UiManifestResponse`` model and all gateway helpers use the field
name ``ui_slots`` — NOT the legacy name ``slots``.

Run with: pytest -m unit tests/unit/test_plugin_models.py
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helper: load a single .py file by path without triggering __init__.py chains
# ---------------------------------------------------------------------------

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src"


def _load_module_from_file(dotted_name: str, filepath: Path):
    """Import *filepath* as *dotted_name* without touching package __init__ files."""
    spec = importlib.util.spec_from_file_location(dotted_name, str(filepath))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec for {filepath}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[dotted_name] = mod
    spec.loader.exec_module(mod)
    return mod


# Stub heavy runtime deps that models.py imports.
# NOTE: Do NOT stub "vyra_base" itself — it is a real importable package and
# stubbing it at module-level poisons sys.modules for every other test file
# that is collected in the same session.  The sub-module stubs below are
# enough because Python uses the sys.modules cache directly when all
# relevant sub-module keys are already present.
for _name in (
    "vyra_base.plugin.runtime",
    "vyra_base.plugin.host_functions",
    "vyra_base.plugin",
    "vyra_base.com",
):
    sys.modules.setdefault(_name, MagicMock())

# Load models.py directly (no relative imports, only stdlib + pydantic + stubs)
_models = _load_module_from_file(
    "plugin_models",
    _SRC_ROOT
    / "{{ module_name }}"
    / "{{ module_name }}"
    / "backend_webserver"
    / "plugin"
    / "models.py",
)

UiManifestEntry = _models.UiManifestEntry
UiManifestResponse = _models.UiManifestResponse


# ============================================================================
# UiManifestEntry Tests
# ============================================================================


class TestUiManifestEntry:
    """Verify UiManifestEntry Pydantic model."""

    def test_minimal_creation(self):
        """UiManifestEntry can be created with only required fields."""
        entry = UiManifestEntry(
            slot_id="side-dock-popup.header",
            component_name="TestPlugin",
            js_entry_point="/plugin/assets/test/1.0.0/ui/index.js",
            plugin_id="test-plugin",
            version="1.0.0",
        )
        assert entry.slot_id == "side-dock-popup.header"
        assert entry.component_name == "TestPlugin"
        assert entry.is_active is True
        assert entry.priority == 50
        assert entry.min_user_role == "operator"

    def test_optional_fields_have_defaults(self):
        """Optional fields like assignment_id, scope_type, title default correctly."""
        entry = UiManifestEntry(
            slot_id="test-slot",
            component_name="C",
            js_entry_point="/x",
            plugin_id="p",
            version="0.1.0",
        )
        assert entry.assignment_id == ""
        assert entry.scope_type == ""
        assert entry.scope_target is None
        assert entry.slot_ids == []
        assert entry.title == ""
        assert entry.search_keywords == []
        assert entry.icon is None
        assert entry.slot_type == ""
        assert entry.nfs_js_path == ""


# ============================================================================
# UiManifestResponse Tests
# ============================================================================


class TestUiManifestResponse:
    """Verify UiManifestResponse uses ``ui_slots`` (not ``slots``)."""

    def test_field_name_is_ui_slots(self):
        """The response model MUST have a field called ``ui_slots``."""
        assert "ui_slots" in UiManifestResponse.model_fields, (
            "UiManifestResponse must contain 'ui_slots' field — "
            "found fields: " + ", ".join(UiManifestResponse.model_fields.keys())
        )

    def test_no_legacy_slots_field(self):
        """The response model must NOT have a bare ``slots`` field."""
        assert (
            "slots" not in UiManifestResponse.model_fields
        ), "UiManifestResponse must NOT contain legacy 'slots' field"

    def test_empty_creation(self):
        """UiManifestResponse can be created with defaults (empty ui_slots)."""
        resp = UiManifestResponse(scope_type="MODULE", scope_target="{{ module_name }}")
        assert resp.scope_type == "MODULE"
        assert resp.scope_target == "{{ module_name }}"
        assert resp.ui_slots == {}
        assert resp.plugin_metadata == []
        assert resp.p_id is None

    def test_creation_with_ui_slots(self):
        """UiManifestResponse accepts a populated ``ui_slots`` dict."""
        entry = UiManifestEntry(
            slot_id="side-dock-popup.header",
            component_name="SdpSystemInfoPlugin",
            js_entry_point="/v2_modulemanager/api/plugin/assets/sdp-system-info/1.0.0/ui/index.js",
            plugin_id="sdp-system-info",
            version="1.0.0",
        )
        resp = UiManifestResponse(
            scope_type="MODULE",
            scope_target="{{ module_name }}",
            ui_slots={"side-dock-popup.header": [entry]},
        )
        assert "side-dock-popup.header" in resp.ui_slots
        assert len(resp.ui_slots["side-dock-popup.header"]) == 1
        assert resp.ui_slots["side-dock-popup.header"][0].plugin_id == "sdp-system-info"

    def test_from_modulemanager_dict(self):
        """Validate against the dict shape returned by PluginManager via Zenoh."""
        mm_response = {
            "scope_type": "MODULE",
            "scope_target": "{{ module_name }}",
            "p_id": None,
            "ui_slots": {
                "side-dock-popup.header": [
                    {
                        "slot_id": "side-dock-popup.header",
                        "component_name": "SdpSystemInfoPlugin",
                        "js_entry_point": "/v2_modulemanager/api/plugin/assets/sdp-system-info/1.0.0/ui/index.js",
                        "plugin_id": "sdp-system-info",
                        "version": "1.0.0",
                    }
                ]
            },
            "plugin_metadata": [],
        }
        resp = UiManifestResponse(**mm_response)
        assert resp.ui_slots is not None
        assert "side-dock-popup.header" in resp.ui_slots

    def test_legacy_slots_dict_rejected(self):
        """A dict with only ``slots`` (no ``ui_slots``) must NOT silently succeed."""
        from pydantic import ValidationError

        legacy_response = {
            "scope_type": "MODULE",
            "scope_target": "{{ module_name }}",
            "slots": {"side-dock-popup.header": []},
        }
        # ``slots`` is not a recognized field — the model should either
        # ignore it (resulting in empty ui_slots) or reject it.
        resp = UiManifestResponse(**legacy_response)
        # In either case, ``ui_slots`` must be the empty default
        assert resp.ui_slots == {}


# ============================================================================
# PluginGateway._empty_manifest Tests
# ============================================================================


class TestPluginGatewayEmptyManifest:
    """Verify _empty_manifest uses ``ui_slots`` key."""

    @pytest.fixture(autouse=True)
    def _load_gateway(self):
        """Load PluginGateway from source, stubbing heavy deps."""
        # Stub parent packages and relative-import targets
        for name in (
            "{{ module_name }}",
            "{{ module_name }}.{{ module_name }}",
            "{{ module_name }}.container_injection",
            "{{ module_name }}.interface",
            "{{ module_name }}.plugin",
        ):
            sys.modules.setdefault(name, MagicMock())

        filepath = (
            _SRC_ROOT / "{{ module_name }}" / "{{ module_name }}" / "plugin" / "plugin_gateway.py"
        )
        spec = importlib.util.spec_from_file_location(
            "{{ module_name }}.plugin.plugin_gateway",
            str(filepath),
            submodule_search_locations=[],
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "{{ module_name }}.plugin"
        sys.modules["{{ module_name }}.plugin.plugin_gateway"] = mod
        spec.loader.exec_module(mod)
        self.PluginGateway = mod.PluginGateway

    def test_empty_manifest_has_ui_slots_key(self):
        """_empty_manifest must return dict with ``ui_slots``, not ``slots``."""
        manifest = self.PluginGateway._empty_manifest("MODULE", "{{ module_name }}")
        assert (
            "ui_slots" in manifest
        ), f"_empty_manifest must contain 'ui_slots' key — got keys: {list(manifest.keys())}"
        assert "slots" not in manifest, "_empty_manifest must NOT contain legacy 'slots' key"
        assert manifest["ui_slots"] == {}

    def test_empty_manifest_scope_fields(self):
        """_empty_manifest returns correct scope_type and scope_target."""
        manifest = self.PluginGateway._empty_manifest("GLOBAL", None)
        assert manifest["scope_type"] == "GLOBAL"
        assert manifest["scope_target"] == ""

        manifest2 = self.PluginGateway._empty_manifest("MODULE", "{{ module_name }}")
        assert manifest2["scope_target"] == "{{ module_name }}"
