import pytest
from jsonrpc_mcp import ToolError

from tools.f1_tools import _validate_decisions, _validate_race_context


def test_validate_race_context_accepts_required_fields():
    _validate_race_context({"circuit": "Monza", "season": 2023})


def test_validate_race_context_accepts_extra_fields():
    _validate_race_context({"circuit": "Monza", "season": 2023, "driver": "NOR", "position": 7})


def test_validate_race_context_rejects_missing_circuit():
    with pytest.raises(ToolError, match=r"missing required field\(s\): \['circuit'\]"):
        _validate_race_context({"season": 2023})


def test_validate_race_context_rejects_missing_season():
    with pytest.raises(ToolError, match=r"missing required field\(s\): \['season'\]"):
        _validate_race_context({"circuit": "Monza"})


def test_validate_decisions_accepts_well_formed_list():
    _validate_decisions(
        [
            {"lap": 18, "tool_used": "get_pit_window", "summary": "Window is laps 19-27."},
            {"lap": 18, "tool_used": "simulate_undercut_overcut", "summary": "No net gain."},
        ]
    )


def test_validate_decisions_accepts_empty_list():
    _validate_decisions([])


def test_validate_decisions_rejects_missing_field_and_names_the_index():
    with pytest.raises(ToolError, match=r"decisions\[1\] is missing required field\(s\): \['summary'\]"):
        _validate_decisions(
            [
                {"lap": 1, "tool_used": "get_pit_window", "summary": "ok"},
                {"lap": 2, "tool_used": "get_pit_window"},
            ]
        )
