"""The demo presets recorded for replay must match what the UI actually sends."""

from __future__ import annotations

import importlib.util
import json
from types import ModuleType

from pac.settings import REPO_ROOT


def _script() -> ModuleType:
    path = REPO_ROOT / "scripts" / "record_demo_presets.py"
    spec = importlib.util.spec_from_file_location("record_demo_presets", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_presets_match_the_ui_source() -> None:
    script = _script()
    position_demo = (REPO_ROOT / "web/src/components/PositionDemo.tsx").read_text()
    classify_panel = (REPO_ROOT / "web/src/components/ClassifyPanel.tsx").read_text()
    for preset in script.POSITION_PRESETS:
        assert json.dumps(preset) in position_demo
    assert json.dumps(script.CLASSIFY_DEFAULT) in classify_panel


def test_tool_response_uses_compact_json_like_the_browser() -> None:
    script = _script()
    event = script.event_for_kind("tool_response", "x", script.ANA)
    assert event.content == '{"title":"Help article","body":"x"}'
