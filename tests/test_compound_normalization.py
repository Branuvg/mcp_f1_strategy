import pytest
from mcp.server.mcpserver.exceptions import ToolError

from tools.f1_tools import _validate_compound


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("SOFT", "SOFT"),
        ("soft", "SOFT"),
        ("S", "SOFT"),
        ("Softs", "SOFT"),
        ("  softs  ", "SOFT"),
        ("MEDIUM", "MEDIUM"),
        ("M", "MEDIUM"),
        ("Mediums", "MEDIUM"),
        ("HARD", "HARD"),
        ("H", "HARD"),
        ("Hards", "HARD"),
        ("INTERMEDIATE", "INTERMEDIATE"),
        ("I", "INTERMEDIATE"),
        ("Inter", "INTERMEDIATE"),
        ("Inters", "INTERMEDIATE"),
        ("Intermediates", "INTERMEDIATE"),
        ("WET", "WET"),
        ("W", "WET"),
        ("Wets", "WET"),
        ("Full Wet", "WET"),
    ],
)
def test_validate_compound_accepts_known_aliases(raw, expected):
    assert _validate_compound(raw) == expected


def test_validate_compound_rejects_unknown_name():
    with pytest.raises(ToolError, match="Invalid compound 'Slick'"):
        _validate_compound("Slick")
