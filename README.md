# mcp_f1_strategy

A local [Model Context Protocol](https://modelcontextprotocol.io/) server that gives an LLM-based race
engineer chatbot real, calculated Formula 1 pit-stop strategy tools: tire degradation modeling, optimal
pit windows, undercut/overcut simulation, and finish-position projection — all computed deterministically
from real session data, not guessed by the language model.

Built for Project 1 ("Uso de un protocolo existente") of CC3067 Redes at Universidad del Valle de
Guatemala. Consumed by [`host_mcp_redes`](../host_mcp_redes), a console MCP host/chatbot built for the
same project.

## Overview

Ask an LLM directly "should I pit now?" and it will guess, based on vague pattern-matching from its
training data. This server instead:

1. Pulls **real lap-by-lap timing data** for a given circuit/season/session from
   [FastF1](https://docs.fastf1.dev/).
2. Fits a **parametric tire degradation curve** (`lap_time = base_pace + degradation_rate * tire_age^k`)
   per compound and circuit, by regression on real stint data.
3. Runs a **deterministic strategy simulation** on top of that curve to answer: what's the optimal pit
   window, does an undercut or overcut work against a specific rival, how do N hypothetical strategies
   compare, and where does a planned strategy project to finish.

The LLM's job is to call the right tool with the right arguments and explain the result to the strategy
engineer — not to invent the numbers.

### Scope note: this is not live timing

FastF1 only exposes **completed** sessions with official data — there is no real-time F1 live-timing feed
here. "Live race" is simulated by replaying a real, past session lap by lap: the `current_lap` / `lap`
parameter plays the role of "where the race currently is." This is an explicit, accepted limitation (see
[Out of scope](#out-of-scope)), not a bug — it's called out here so the scope is unambiguous.

## Tools (9)

All tools take a FastF1-style `circuit` name (e.g. `"Monza"`, `"Silverstone"`, `"Bahrain"` — FastF1 does
fuzzy matching on event names, so close spellings usually resolve, but a genuinely wrong circuit will
eventually fail to find any session), a `season` (year), and a `session` code (`"FP1"`, `"FP2"`, `"FP3"`,
`"Q"`, `"R"`) where applicable. Drivers use FastF1's 3-letter codes (`"LEC"`, `"VER"`, `"HAM"`, ...).

| Tool | Purpose |
|---|---|
| `get_race_state` | Positions, gaps, compound and tire age for every driver at a given lap. |
| `get_tire_degradation_curve` | Calibrated degradation model (base pace + rate) for a compound at a circuit. |
| `get_pit_loss_time` | Time lost by pitting at a circuit, from real data when available. |
| `get_pit_window` | Optimal pit-stop window for a driver, plus a traffic-risk read for the pit exit. |
| `simulate_undercut_overcut` | Compares pitting before (undercut) vs. after (overcut) a named rival. |
| `compare_strategy_options` | Projects total time for N arbitrary hypothetical strategies. |
| `get_historical_strategies` | Real stint breakdown and finish position from past races at a circuit. |
| `predict_finish_position` | Projects finishing position/time for a planned strategy for the rest of the race. |
| `generate_strategy_report` | Formats a Markdown report from a host/LLM-curated decision log (no summarization). |

Full input/output JSON shapes for every tool are specified in
[`mcp_f1_strategy_spec.md`](./mcp_f1_strategy_spec.md) (section 8) — that document is the authoritative
protocol specification for this server, kept in the repo alongside the code.

### Error vs. warning policy

Every tool follows the same rule:

- **Explicit tool error** (`isError: true`, clear message) when the base data genuinely doesn't exist —
  the driver never took part in the session, the circuit/season/session combination has no data in
  FastF1, or a requested lap is out of range. The LLM is expected to relay this to the user, not paper
  over it.
- **Successful result with a `warning` field** when the data exists but the model's confidence is low
  (e.g. a compound was barely used at that circuit, so the degradation fit has a low `r_squared` and
  small `sample_size_laps`). The raw confidence indicators (`r_squared`, `sample_size_laps`,
  `data_source`) are always included so the LLM — and the engineer — can judge for themselves.

## Methodology & known limitations

- **Degradation curves are fit per compound + circuit, pooling laps across every driver** who used that
  compound in the queried event (race + practice sessions) and, if needed, the same circuit in up to two
  prior seasons. This is what the spec asks for, but it means the fit mixes different cars/drivers/fuel
  loads together — real F1 lap times are dominated by fuel burn-off, traffic and driver pace, not just
  tire wear, so `r_squared` for a single event is often genuinely low (this has been observed directly
  against real data, e.g. 2023 Monza MEDIUM: `r_squared ≈ 0.04`). That's not a bug: it's why the
  `warning`/`r_squared`/`sample_size_laps` transparency fields exist, instead of a single opaque number.
- **Pit loss time** is the median of real in-lap+out-lap time lost (relative to that driver's own
  green-flag pace) across every stop in the queried race; it falls back to a generic ~22.5s estimate
  below 3 real samples.
- **Undercut/overcut and pit-window simulation** use a fixed short evaluation horizon (6 laps) and assume
  the tire fitted after any hypothetical stop is FastF1's compound-agnostic "alternative" pick (the harder
  of the two compounds not currently mounted, unless already on `HARD`, in which case `MEDIUM`).
- **`predict_finish_position`** assumes every rival holds their last 3 laps' average pace for the rest of
  the race with no further pit stops, and that a faster projected time converts directly into position —
  it does not model overtaking difficulty. These are declared explicitly in the tool's own
  `key_assumptions` output field, per spec.

## Prerequisites

- **Python 3.10+** (developed and tested on 3.14)
- **[uv](https://docs.astral.sh/uv/)** for dependency/environment management
- **Internet access** on first query per circuit/season/session (FastF1 downloads and caches official
  timing data under `./cache/`; repeat queries to the same session are served from that local cache with
  no network call)

## Installation

```bash
git clone <your-repo-url>
cd mcp_f1_strategy
uv sync
```

`uv sync` installs the MCP Python SDK, `fastf1`, `numpy`, and the test dependencies declared in
`pyproject.toml`.

## Running standalone

The server speaks MCP over stdio — it isn't meant to be run interactively by itself, but you can smoke-test
it directly:

```bash
uv run python src/server.py
```

It will sit there waiting for JSON-RPC messages on stdin (that's expected — this is how an MCP host talks
to it). Press Ctrl+C to stop it.

## Wiring into `host_mcp_redes`

Add an entry to `host_mcp_redes/mcp_config.json` pointing at this repository (adjust the path to wherever
you cloned it — the example below assumes it's a sibling directory of `host_mcp_redes`, as in this
project's layout):

```json
{
  "mcpServers": {
    "f1_strategy": {
      "command": "uv",
      "args": [
        "run",
        "--project", "../mcp_f1_strategy",
        "python", "../mcp_f1_strategy/src/server.py"
      ]
    }
  }
}
```

No changes to `host_mcp_redes`'s Python code are needed — it discovers this server's 9 tools via MCP's
`list_tools`, the same way it already does for the Filesystem and Git MCP servers.

### Example scenario

With the host running and this server wired in:

```
You: I'm racing at Monza 2023, currently on lap 20 as LEC on 20-lap-old MEDIUM tires.
     What's my pit window, and would an undercut on VER make sense right now?
```

Claude will call `get_pit_window` and `simulate_undercut_overcut` (chaining `get_race_state` /
`get_tire_degradation_curve` / `get_pit_loss_time` as needed for context), then explain the recommendation
— including surfacing any low-confidence warning honestly rather than hiding it.

## Running tests

```bash
uv run pytest
```

Tests cover the pure-math layer (`models/degradation.py`, `models/pit_loss.py`, `models/strategy_sim.py`,
`reports/report_builder.py`) against synthetic, hand-checkable inputs — this layer has no FastF1/MCP
dependency by design, so it needs no network access or fixtures to test. The data (`data/`) and protocol
(`tools/`) layers were validated manually against real FastF1 sessions (see the spec's methodology
section above for what was observed).

## Project structure

```
mcp_f1_strategy/
├── src/
│   ├── server.py              # MCP entrypoint: registers the 9 tools, stdio transport
│   ├── data/
│   │   └── fastf1_client.py   # the only module that imports fastf1/pandas
│   ├── models/
│   │   ├── degradation.py     # tire degradation curve fitting (pure math)
│   │   ├── pit_loss.py        # pit loss time estimation (pure math)
│   │   └── strategy_sim.py    # pit window / undercut-overcut / strategy simulation engine
│   ├── tools/
│   │   └── f1_tools.py        # the 9 MCP tool definitions; only layer that knows MCP
│   └── reports/
│       └── report_builder.py  # generate_strategy_report Markdown formatting
├── cache/                      # FastF1 local cache (gitignored)
├── tests/                      # pytest unit tests for models/ and reports/
├── mcp_f1_strategy_spec.md     # full protocol specification (inputs/outputs per tool)
├── pyproject.toml
└── README.md
```

## Out of scope

- No real live timing — FastF1 exposes completed sessions only (see [Scope note](#scope-note-this-is-not-live-timing)).
- No custom database for historicals — `get_historical_strategies` reads directly through FastF1's own cache.
- No special fallback for network/FastF1 failures — the host chatbot already requires internet for the LLM API, so this doesn't add a new failure mode.
- `predict_finish_position` does not model safety cars, VSC, weather changes, or non-deterministic rival behavior — declared explicitly in its `key_assumptions` output.
- No HTTP/SSE transport — this server is local/stdio only; a remote MCP server is a separate deliverable of this project.
