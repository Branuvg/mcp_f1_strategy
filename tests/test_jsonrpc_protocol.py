"""End-to-end test of the hand-rolled JSON-RPC/MCP protocol layer
(`jsonrpc_mcp.py`), exercised against the real 9 registered tools — no `mcp`
SDK involved. Verifies the lifecycle (`initialize`, `notifications/initialized`,
`tools/list`, `tools/call`) speaks well-formed JSON-RPC 2.0 end to end.
"""

from __future__ import annotations

from jsonrpc_mcp import MCPServer
from tools.f1_tools import register_tools


def make_server() -> MCPServer:
    server = MCPServer(name="f1-strategy-test")
    register_tools(server, cache_dir="cache")
    return server


def test_initialize_returns_protocol_version_and_server_info():
    server = make_server()

    response = server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize"})

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    assert response["result"]["protocolVersion"] == "2025-06-18"
    assert response["result"]["serverInfo"]["name"] == "f1-strategy-test"


def test_initialized_notification_gets_no_response():
    server = make_server()

    response = server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"})

    assert response is None


def test_tools_list_exposes_all_nine_tools_with_schemas():
    server = make_server()

    response = server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = response["result"]["tools"]
    names = {t["name"] for t in tools}

    assert names == {
        "get_race_state",
        "get_tire_degradation_curve",
        "get_pit_loss_time",
        "get_pit_window",
        "simulate_undercut_overcut",
        "compare_strategy_options",
        "get_historical_strategies",
        "predict_finish_position",
        "generate_strategy_report",
    }
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
        assert "properties" in tool["inputSchema"]


def test_tools_call_unknown_tool_returns_error_result_not_exception():
    server = make_server()

    response = server.handle_message(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "does_not_exist", "arguments": {}}}
    )

    assert response["result"]["isError"] is True
    assert "does_not_exist" in response["result"]["content"][0]["text"]


def test_unknown_method_returns_jsonrpc_error():
    server = make_server()

    response = server.handle_message({"jsonrpc": "2.0", "id": 4, "method": "not/a/real/method"})

    assert response["error"]["code"] == -32601
    assert response["id"] == 4


def test_generate_strategy_report_round_trip_through_tools_call():
    """A tool that needs no network/FastF1 access, exercised through the
    full JSON-RPC dispatch path (not called directly)."""
    server = make_server()

    response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "generate_strategy_report",
                "arguments": {
                    "race_context": {"circuit": "Monza", "season": 2023},
                    "decisions": [{"lap": 20, "tool_used": "simulate_undercut_overcut", "summary": "Stayed out."}],
                },
            },
        }
    )

    assert response["result"].get("isError") is not True
    assert "Monza" in response["result"]["content"][0]["text"]
