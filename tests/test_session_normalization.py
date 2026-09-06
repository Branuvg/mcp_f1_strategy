import pytest
from mcp.server.mcpserver.exceptions import ToolError

from tools.f1_tools import _normalize_session_code


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("R", "R"),
        ("r", "R"),
        ("Race", "R"),
        ("RACE", "R"),
        ("  race  ", "R"),
        ("Q", "Q"),
        ("Qualifying", "Q"),
        ("Qualification", "Q"),
        ("Quali", "Q"),
        ("FP1", "FP1"),
        ("Practice 1", "FP1"),
        ("practice1", "FP1"),
        ("Free Practice 2", "FP2"),
        ("Practice 3", "FP3"),
    ],
)
def test_normalize_session_code_accepts_known_names(raw, expected):
    assert _normalize_session_code(raw) == expected


def test_normalize_session_code_rejects_unknown_name():
    with pytest.raises(ToolError, match="Invalid session 'Sprint'"):
        _normalize_session_code("Sprint")
