"""Deterministic Markdown formatting for `generate_strategy_report`.

This module does not summarize or interpret anything: it receives an
already-curated list of decisions (produced by the LLM/host during the
conversation) and only formats it. No MCP, no FastF1, no business logic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, TypedDict


class Decision(TypedDict):
    lap: int
    tool_used: str
    summary: str


class RaceContext(TypedDict):
    circuit: str
    season: int


def build_report(race_context: RaceContext, decisions: list[Decision]) -> dict[str, Any]:
    circuit = race_context["circuit"]
    season = race_context["season"]
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        f"# Strategy Report — {circuit} {season}",
        "",
        f"*Generated: {generated_at}*",
        "",
        "## Decision Log",
        "",
    ]

    if not decisions:
        lines.append("_No strategy decisions were recorded for this session._")
    else:
        ordered = sorted(decisions, key=lambda d: d["lap"])
        for decision in ordered:
            lines.append(f"### Lap {decision['lap']} — `{decision['tool_used']}`")
            lines.append("")
            lines.append(decision["summary"])
            lines.append("")

    markdown_report = "\n".join(lines).rstrip() + "\n"

    safe_circuit = "".join(c if c.isalnum() else "_" for c in circuit.lower())
    filename_suggestion = f"strategy_report_{safe_circuit}_{season}.md"

    return {"markdown_report": markdown_report, "filename_suggestion": filename_suggestion}
