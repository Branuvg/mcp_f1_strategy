# mcp_f1_strategy

A local [Model Context Protocol](https://modelcontextprotocol.io/) server that gives an LLM-based race
engineer chatbot real, calculated Formula 1 pit-stop strategy tools: tire degradation modeling, optimal
pit windows, undercut/overcut simulation, and finish-position projection - all computed deterministically
from real session data, not guessed by the language model.

Built for Project 1 ("Uso de un protocolo existente") of CC3067 Redes at Universidad del Valle de
Guatemala. It's a standard local (stdio) MCP server, so it works with **any** MCP-compatible host -
Claude Desktop, a custom console chatbot, or any other client that speaks the protocol - not just one
specific host application.

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
engineer - not to invent the numbers.

### Scope note: this is not live timing

FastF1 only exposes **completed** sessions with official data - there is no real-time F1 live-timing feed
here. "Live race" is simulated by replaying a real, past session lap by lap: the `current_lap` / `lap`
parameter plays the role of "where the race currently is." This is an explicit, accepted limitation (see
[Out of scope](#out-of-scope)), not a bug - it's called out here so the scope is unambiguous.

## Tools (9)

All tools take a FastF1-style `circuit` name (e.g. `"Monza"`, `"Silverstone"`, `"Bahrain"` - FastF1 does
fuzzy matching on event names, so close spellings usually resolve, but a genuinely wrong circuit will
eventually fail to find any session), a `season` (year), and a `session` code (`"FP1"`, `"FP2"`, `"FP3"`,
`"Q"`, `"R"`) where applicable - common natural-language names are also accepted and normalized
(`"Race"` -> `"R"`, `"Qualifying"`/`"Quali"` -> `"Q"`, `"Practice 1"` -> `"FP1"`, case-insensitive),
since an LLM caller is more likely to guess those than the short code. Tire `compound` similarly accepts
single-letter TV-graphic codes and plurals (`"S"`/`"Softs"` -> `"SOFT"`, `"M"` -> `"MEDIUM"`, etc.).
Drivers can be given as FastF1's 3-letter code (`"LEC"`), a car number (`"16"`), a last or full name
(`"Leclerc"`, `"Charles Leclerc"`), or a close misspelling of one — resolved against that session's actual
entry list, with a clear error listing the session's real drivers if nothing matches.

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

Full input/output JSON shapes for every tool are below.

### Tool reference

#### `get_race_state`
Raw state of the session at a given lap: positions, gaps, compound and tire age for every driver.
```
input:  { circuit: str, season: int, session: "R"|"Q"|"FP1"|"FP2"|"FP3", lap: int }
output: {
  lap: int,
  drivers: [
    { driver: str, position: int, gap_to_leader_s: float, gap_to_ahead_s: float,
      compound: str, tire_age_laps: int }
  ]
}
```

#### `get_tire_degradation_curve`
Calibrated degradation model parameters for a compound + circuit.
```
input:  { compound: "SOFT"|"MEDIUM"|"HARD"|"INTERMEDIATE"|"WET", circuit: str, season?: int }
output: {
  compound: str, circuit: str,
  base_pace_s: float,
  degradation_rate_s_per_lap: float,
  model_type: "linear"|"quadratic",
  r_squared: float,
  sample_size_laps: int,
  data_source: "fastf1_real" | "insufficient_data_fallback",
  warning?: str
}
```

#### `get_pit_loss_time`
```
input:  { circuit: str, season?: int }
output: { circuit: str, pit_loss_time_s: float, source: "fastf1_real"|"generic_fallback", warning?: str }
```

#### `get_pit_window`
Optimal pit-stop window for a driver, with traffic risk at the pit exit.
```
input:  { driver: str, circuit: str, season: int, session: str, current_lap: int }
output: {
  driver: str, current_lap: int,
  optimal_window: { start_lap: int, end_lap: int },
  reasoning: str,
  traffic_risk: "low"|"medium"|"high",
  traffic_risk_reason: str,
  warning?: str
}
```

#### `simulate_undercut_overcut`
Compares pitting before (undercut) or after (overcut) a named rival.
```
input:  { own_driver: str, rival_driver: str, circuit: str, season: int, session: str, current_lap: int }
output: {
  undercut: { pit_lap: int, projected_time_delta_s: float, net_position_gain: bool },
  overcut:  { pit_lap: int, projected_time_delta_s: float, net_position_gain: bool },
  recommendation: "undercut"|"overcut"|"stay_out"|"no_clear_advantage",
  reasoning: str
}
```

#### `compare_strategy_options`
Generalizes undercut/overcut: compares N hypothetical strategies (pit laps + compounds) for one driver.
```
input:  {
  driver: str, circuit: str, season: int, session: str, current_lap: int,
  strategies: [ { label: str, pit_laps: [int], compounds: [str] } ]
}
output: {
  results: [ { label: str, projected_total_time_s: float } ],
  best_strategy: str
}
```

#### `get_historical_strategies`
```
input:  { circuit: str, seasons?: [int], drivers?: [str] }
output: {
  circuit: str,
  races: [
    { season: int, driver: str,
      stints: [ { compound: str, start_lap: int, end_lap: int } ],
      finish_position: int }
  ]
}
```

#### `predict_finish_position`
Projects finishing position/time for a planned strategy for the rest of the race.
```
input:  { driver: str, circuit: str, season: int, session: str, current_lap: int,
          planned_strategy: { pit_laps: [int], compounds: [str] } }
output: {
  driver: str,
  projected_finish_position: int,
  projected_total_time_s: float,
  confidence: "low"|"medium"|"high",
  key_assumptions: [str]
}
```
`key_assumptions` always declares the model's limitations explicitly (e.g. "assumes constant rival pace",
"does not consider safety car/VSC", "does not consider changing weather").

#### `generate_strategy_report`
Formats a Markdown report from an already-curated decision log. Does not summarize or interpret - that's
the LLM/host's job during the conversation; this tool only formats deterministically.
```
input:  {
  race_context: { circuit: str, season: int },
  decisions: [ { lap: int, tool_used: str, summary: str } ]
}
output: { markdown_report: str, filename_suggestion: str }
```

### Error vs. warning policy

Every tool follows the same rule:

- **Explicit tool error** (`isError: true`, clear message) when the base data genuinely doesn't exist -
  the driver never took part in the session, the circuit/season/session combination has no data in
  FastF1, or a requested lap is out of range. The LLM is expected to relay this to the user, not paper
  over it.
- **Successful result with a `warning` field** when the data exists but the model's confidence is low
  (e.g. a compound was barely used at that circuit, so the degradation fit has a low `r_squared` and
  small `sample_size_laps`). The raw confidence indicators (`r_squared`, `sample_size_laps`,
  `data_source`) are always included so the LLM - and the engineer - can judge for themselves.

## Methodology & known limitations

- **Degradation curves are fit per compound + circuit, pooling laps across every driver** who used that
  compound in the queried event (race + practice sessions) and, if needed, the same circuit in up to two
  prior seasons. This is what the spec asks for, but it means the fit mixes different cars/drivers/fuel
  loads together - real F1 lap times are dominated by fuel burn-off, traffic and driver pace, not just
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
  the race with no further pit stops, and that a faster projected time converts directly into position -
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

The server speaks MCP over stdio - it isn't meant to be run interactively by itself, but you can smoke-test
it directly:

```bash
uv run python src/server.py
```

It will sit there waiting for JSON-RPC messages on stdin (that's expected - this is how an MCP host talks
to it). Press Ctrl+C to stop it.

## Adding this server to an MCP host

This server uses the **stdio** transport, so any MCP host that can launch a local subprocess and speak
MCP over its stdin/stdout can use it. Almost every MCP host (Claude Desktop, Cursor, and most custom
chatbot hosts, including student-built ones for this course) reads its server list from a JSON config
shaped like this - often called `mcpServers`:

```json
{
  "mcpServers": {
    "f1_strategy": {
      "command": "uv",
      "args": [
        "run",
        "--project", "/absolute/path/to/mcp_f1_strategy",
        "python", "/absolute/path/to/mcp_f1_strategy/src/server.py"
      ]
    }
  }
}
```

Replace `/absolute/path/to/mcp_f1_strategy` with wherever you cloned this repository (e.g.
`C:\Users\you\projects\mcp_f1_strategy` on Windows, `/home/you/projects/mcp_f1_strategy` on Linux/macOS).
Using an absolute path means the entry works regardless of the host's own working directory. `uv` handles
creating the virtual environment and installing dependencies on first launch - no manual `uv sync` step is
required by the host, though running it once yourself (see [Installation](#installation)) is a good sanity
check.

If your host doesn't use `uv`, the equivalent is just: activate this project's virtual environment, then
run `python src/server.py` from inside `mcp_f1_strategy/` (or with `PYTHONPATH` pointed at its `src/`
directory).

**For Claude Desktop specifically**, this same JSON block goes under `mcpServers` in its config file
(`claude_desktop_config.json` - on Windows, `%APPDATA%\Claude\claude_desktop_config.json`; on macOS,
`~/Library/Application Support/Claude/claude_desktop_config.json`), then restart Claude Desktop.

Once connected, the host discovers all 9 tools via MCP's `list_tools` - no code changes to the host are
needed.

### Example scenario

With this server wired into your host of choice:

```
You: I'm racing at Monza 2023, currently on lap 20 as LEC on 20-lap-old MEDIUM tires.
     What's my pit window, and would an undercut on VER make sense right now?
```

The LLM will call `get_pit_window` and `simulate_undercut_overcut` (chaining `get_race_state` /
`get_tire_degradation_curve` / `get_pit_loss_time` as needed for context), then explain the recommendation
- including surfacing any low-confidence warning honestly rather than hiding it.

## Running tests

```bash
uv run pytest
```

Tests cover the pure-math layer (`models/degradation.py`, `models/pit_loss.py`, `models/strategy_sim.py`,
`reports/report_builder.py`) against synthetic, hand-checkable inputs - this layer has no FastF1/MCP
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
├── pyproject.toml
└── README.md
```

## Out of scope

- No real live timing - FastF1 exposes completed sessions only (see [Scope note](#scope-note-this-is-not-live-timing)).
- No custom database for historicals - `get_historical_strategies` reads directly through FastF1's own cache.
- No special fallback for network/FastF1 failures - any MCP host using this server already requires internet for its own LLM API calls, so this doesn't add a new failure mode.
- `predict_finish_position` does not model safety cars, VSC, weather changes, or non-deterministic rival behavior - declared explicitly in its `key_assumptions` output.
- No HTTP/SSE transport - this server is local/stdio only; a remote MCP server is a separate deliverable of this project.
