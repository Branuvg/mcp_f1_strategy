from reports.report_builder import build_report


def test_report_orders_decisions_by_lap_and_formats_markdown():
    result = build_report(
        {"circuit": "Monza", "season": 2023},
        [
            {"lap": 30, "tool_used": "simulate_undercut_overcut", "summary": "Undercut recommended."},
            {"lap": 12, "tool_used": "get_pit_window", "summary": "Window is laps 12-15."},
        ],
    )

    markdown = result["markdown_report"]
    assert "Monza 2023" in markdown
    assert markdown.index("Lap 12") < markdown.index("Lap 30")
    assert result["filename_suggestion"] == "strategy_report_monza_2023.md"


def test_report_handles_no_decisions():
    result = build_report({"circuit": "Spa", "season": 2024}, [])
    assert "No strategy decisions" in result["markdown_report"]
