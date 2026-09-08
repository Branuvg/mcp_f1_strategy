"""Minimal hand-rolled MCP server: JSON-RPC 2.0 over the `stdio` transport.

Implements just the lifecycle this project needs directly against the wire
format described in the MCP specification (2025-06-18) — `initialize`,
`notifications/initialized`, `tools/list`, `tools/call` — with newline-
delimited JSON-RPC 2.0 messages on stdin/stdout. No dependency on the `mcp`
SDK package: this is the raw-JSON-RPC implementation requested by the
project's extra-credit item (section 4.1).

Kept intentionally close in shape to the tool registration API the rest of
this codebase (`tools/f1_tools.py`) was already written against
(`@server.tool()` decorator, `ToolError` for tool-facing failures), so the
protocol layer could be swapped out without touching business logic.
"""

from __future__ import annotations

import inspect
import json
import sys
import types
import typing
from dataclasses import dataclass, field
from typing import Any, Callable

JSONRPC_VERSION = "2.0"
PROTOCOL_VERSION = "2025-06-18"

# Standard JSON-RPC 2.0 error codes actually used by this server.
PARSE_ERROR = -32700
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603


class ToolError(Exception):
    """Raised by a tool implementation to report a client-facing tool error.

    Caught by the dispatcher and turned into a `tools/call` result with
    `isError: true`, instead of an unhandled exception / stack trace leaking
    to the client.
    """


@dataclass
class _RegisteredTool:
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    func: Callable[..., Any] | None = None


def _schema_for_annotation(annotation: Any) -> dict[str, Any]:
    """Best-effort JSON Schema for a single parameter's Python type hint."""
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {}

    origin = typing.get_origin(annotation)

    # `X | None` (PEP 604) and `typing.Optional[X]`/`typing.Union[X, None]`:
    # schema for the non-None member; a bare `X | Y` union falls back to `{}`.
    if origin is typing.Union or origin is types.UnionType:
        members = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(members) == 1:
            return _schema_for_annotation(members[0])
        return {}

    if origin in (list, typing.List):
        item_args = typing.get_args(annotation)
        item_schema = _schema_for_annotation(item_args[0]) if item_args else {}
        return {"type": "array", "items": item_schema}

    if origin in (dict, typing.Dict):
        return {"type": "object"}

    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}

    return {}


def _build_input_schema(func: Callable[..., Any]) -> dict[str, Any]:
    """Derive a JSON Schema object for a tool function's parameters."""
    signature = inspect.signature(func)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param_name, param in signature.parameters.items():
        properties[param_name] = _schema_for_annotation(param.annotation)
        if param.default is inspect.Parameter.empty:
            required.append(param_name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _description_for(func: Callable[..., Any]) -> str:
    """First paragraph of the tool function's docstring, single-lined."""
    doc = inspect.getdoc(func) or ""
    first_paragraph = doc.split("\n\n", 1)[0]
    return " ".join(line.strip() for line in first_paragraph.splitlines()).strip()


def _jsonrpc_response(msg_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": msg_id, "result": result}


def _jsonrpc_error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": msg_id, "error": {"code": code, "message": message}}


class MCPServer:
    """A minimal MCP server: tool registration plus the JSON-RPC stdio loop."""

    def __init__(self, name: str, instructions: str = "") -> None:
        self.name = name
        self.instructions = instructions
        self._tools: dict[str, _RegisteredTool] = {}

    def tool(self) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator: register a function as an MCP tool.

        The tool's name is the function's `__name__`, its description comes
        from the first paragraph of its docstring, and its `inputSchema` is
        derived from the function's type-hinted signature.
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self._tools[func.__name__] = _RegisteredTool(
                name=func.__name__,
                description=_description_for(func),
                input_schema=_build_input_schema(func),
                func=func,
            )
            return func

        return decorator

    # ------------------------------------------------------------------ #
    #  MCP lifecycle / JSON-RPC method dispatch
    # ------------------------------------------------------------------ #

    def _handle_initialize(self, msg_id: Any) -> dict[str, Any]:
        return _jsonrpc_response(
            msg_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self.name, "version": "0.1.0"},
            },
        )

    def _handle_tools_list(self, msg_id: Any) -> dict[str, Any]:
        tools = [
            {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
            for t in self._tools.values()
        ]
        return _jsonrpc_response(msg_id, {"tools": tools})

    def _handle_tools_call(self, msg_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        tool_name = params.get("name")
        arguments: dict[str, Any] = params.get("arguments") or {}

        tool = self._tools.get(tool_name) if tool_name else None
        if tool is None or tool.func is None:
            return _jsonrpc_response(
                msg_id,
                {"content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}], "isError": True},
            )

        try:
            result = tool.func(**arguments)
            text = result if isinstance(result, str) else json.dumps(result, indent=2)
            return _jsonrpc_response(msg_id, {"content": [{"type": "text", "text": text}]})
        except ToolError as exc:
            return _jsonrpc_response(
                msg_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True}
            )
        except Exception as exc:  # never leak a raw traceback to the client
            sys.stderr.write(f"Unexpected error in tool '{tool_name}': {exc}\n")
            sys.stderr.flush()
            return _jsonrpc_response(
                msg_id,
                {"content": [{"type": "text", "text": f"Internal error executing '{tool_name}'."}], "isError": True},
            )

    def handle_message(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        """Route one parsed JSON-RPC message; return the response, or None
        for notifications (no `id`) which never get a JSON-RPC reply."""
        method = msg.get("method", "")
        msg_id = msg.get("id")

        if method == "initialize":
            return self._handle_initialize(msg_id)
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return self._handle_tools_list(msg_id)
        if method == "tools/call":
            return self._handle_tools_call(msg_id, msg.get("params") or {})

        if msg_id is None:
            return None  # unknown notification: nothing to reply with
        return _jsonrpc_error(msg_id, METHOD_NOT_FOUND, f"Method not found: {method}")

    # ------------------------------------------------------------------ #
    #  Transport: newline-delimited JSON-RPC over stdio
    # ------------------------------------------------------------------ #

    def run(self, transport: str = "stdio") -> None:
        if transport != "stdio":
            raise ValueError(f"Unsupported transport: {transport!r} (only 'stdio' is implemented)")

        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue

            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                sys.stderr.write(f"Parse error: {exc}\n")
                sys.stderr.flush()
                sys.stdout.write(json.dumps(_jsonrpc_error(None, PARSE_ERROR, "Parse error")) + "\n")
                sys.stdout.flush()
                continue

            try:
                response = self.handle_message(msg)
            except Exception as exc:
                sys.stderr.write(f"Internal error handling message: {exc}\n")
                sys.stderr.flush()
                response = _jsonrpc_error(msg.get("id"), INTERNAL_ERROR, "Internal server error")

            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
