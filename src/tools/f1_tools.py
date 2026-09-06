"""The 9 MCP tools: the only layer in this project that knows about the MCP
protocol. Translates between tool input/output and `data/`/`models/`, and is
responsible for the error-vs-warning policy described in the spec (section 7):
raise `ToolError` when required base data is missing, return a normal result
with a `warning` field when the data exists but confidence is low.
"""

from __future__ import annotations

import functools
from datetime import date
from typing import Any, Callable, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from data import fastf1_client
from models import strategy_sim
from models.degradation import DegradationModel, fit_degradation_curve
from models.pit_loss import PitLossEstimate, estimate_pit_loss_time
from reports import report_builder

_F = TypeVar("_F", bound=Callable[..., Any])

# Fitted models / pit-loss estimates are expensive (they trigger FastF1 loads
# and a regression), and several tools reuse the same circuit/season/compound
# within one strategy conversation.
_degradation_cache: dict[tuple[str, int, str], DegradationModel] = {}
_pit_loss_cache: dict[tuple[str, int], PitLossEstimate] = {}


def _safe(fn: _F) -> _F:
    """Turn the data layer's `DataNotAvailableError`/`ValueError` into the
    explicit MCP tool error the spec asks for, instead of an unhandled crash.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except fastf1_client.DataNotAvailableError as exc:
            raise ToolError(str(exc)) from exc
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    return wrapper  # type: ignore[return-value]


def _validate_session_code(session: str) -> None:
    if session not in fastf1_client.VALID_SESSIONS:
        raise ToolError(
            f"Invalid session '{session}'. Must be one of {sorted(fastf1_client.VALID_SESSIONS)}."
        )


def _validate_compound(compound: str) -> str:
    compound = compound.upper()
    if compound not in fastf1_client.VALID_COMPOUNDS:
        raise ToolError(
            f"Invalid compound '{compound}'. Must be one of {sorted(fastf1_client.VALID_COMPOUNDS)}."
        )
    return compound


def _resolve_primary_season(circuit: str, season: int | None) -> int:
    """Resolve an omitted `season` to the most recent one that actually has a
    race session for this circuit in FastF1 (this year, then up to two years
    back). Raises `ToolError` if none of those have any data at all — that's
    a genuinely invalid/misspelled circuit, not a low-confidence case.
    """
    if season is not None:
        # Validate eagerly so a bad circuit/season fails clearly, right here.
        fastf1_client.load_session(season, circuit, "R")
        return season

    this_year = date.today().year
    for candidate in (this_year, this_year - 1, this_year - 2):
        if fastf1_client.try_load_session(candidate, circuit, "R") is not None:
            return candidate

    raise ToolError(
        f"No FastF1 race session found for circuit '{circuit}' in the last 3 seasons. "
        "Check the circuit name (FastF1 event naming, e.g. 'Monza', 'Silverstone', 'Bahrain')."
    )


def _get_degradation_model(circuit: str, season: int, compound: str) -> DegradationModel:
    key = (circuit, season, compound)
    if key not in _degradation_cache:
        samples, reference_pace = fastf1_client.gather_degradation_samples(circuit, season, compound)
        _degradation_cache[key] = fit_degradation_curve(compound, circuit, samples, reference_pace)
    return _degradation_cache[key]


def _get_pit_loss(circuit: str, season: int) -> PitLossEstimate:
    key = (circuit, season)
    if key not in _pit_loss_cache:
        session = fastf1_client.load_session(season, circuit, "R")
        samples = fastf1_client.gather_pit_stop_samples(session)
        _pit_loss_cache[key] = estimate_pit_loss_time(circuit, samples)
    return _pit_loss_cache[key]


def _degradation_model_to_dict(model: DegradationModel) -> dict[str, Any]:
    return {
        "compound": model.compound,
        "circuit": model.circuit,
        "base_pace_s": model.base_pace_s,
        "degradation_rate_s_per_lap": model.degradation_rate_s_per_lap,
        "model_type": model.model_type,
        "r_squared": model.r_squared,
        "sample_size_laps": model.sample_size_laps,
        "data_source": model.data_source,
    }


def register_tools(server: MCPServer, cache_dir: str) -> None:
    fastf1_client.enable_cache(cache_dir)

    @server.tool()
    @_safe
    def get_race_state(circuit: str, season: int, session: str, lap: int) -> dict[str, Any]:
        """Raw session snapshot at a given lap: position, gap to leader/car
        ahead, tire compound and tire age for every driver still classified.
        """
        _validate_session_code(session)
        sess = fastf1_client.load_session(season, circuit, session)
        drivers = fastf1_client.get_race_state_rows(sess, lap)
        return {"lap": lap, "drivers": drivers}

    @server.tool()
    @_safe
    def get_tire_degradation_curve(
        compound: str, circuit: str, season: int | None = None
    ) -> dict[str, Any]:
        """Calibrated tire degradation model (base pace + degradation rate)
        for a compound at a circuit, fit on real FastF1 lap data.
        """
        compound = _validate_compound(compound)
        resolved_season = _resolve_primary_season(circuit, season)
        model = _get_degradation_model(circuit, resolved_season, compound)
        result = _degradation_model_to_dict(model)
        if model.is_low_confidence:
            result["warning"] = (
                f"Low-confidence fit (r_squared={model.r_squared}, "
                f"sample_size_laps={model.sample_size_laps}); treat this curve as indicative only."
            )
        return result

    @server.tool()
    @_safe
    def get_pit_loss_time(circuit: str, season: int | None = None) -> dict[str, Any]:
        """Time lost by pitting at this circuit (in-lap + pit lane + out-lap,
        minus a green-flag lap), from real FastF1 data when available.
        """
        resolved_season = _resolve_primary_season(circuit, season)
        estimate = _get_pit_loss(circuit, resolved_season)
        result = {
            "circuit": estimate.circuit,
            "pit_loss_time_s": estimate.pit_loss_time_s,
            "source": estimate.source,
        }
        if estimate.source == "generic_fallback":
            result["warning"] = (
                f"Only {estimate.sample_size} real pit-stop sample(s) found for this circuit; "
                "using a generic fallback estimate."
            )
        return result

    @server.tool()
    @_safe
    def get_pit_window(
        driver: str, circuit: str, season: int, session: str, current_lap: int
    ) -> dict[str, Any]:
        """Optimal pit window for `driver`, weighing tire degradation against
        pit loss time, plus a traffic-risk read on the car right behind.
        """
        _validate_session_code(session)
        sess = fastf1_client.load_session(season, circuit, session)
        state = fastf1_client.get_driver_state_at_lap(sess, driver, current_lap)
        total_laps = fastf1_client.get_total_laps(sess)

        fresh_compound = strategy_sim.pick_alternative_compound(state["compound"])
        current_model = _get_degradation_model(circuit, season, state["compound"])
        fresh_model = _get_degradation_model(circuit, season, fresh_compound)
        pit_loss = _get_pit_loss(circuit, season)

        window = strategy_sim.find_optimal_pit_window(
            current_model,
            fresh_model,
            state["tire_age_laps"],
            current_lap,
            total_laps,
            pit_loss.pit_loss_time_s,
        )

        rows = fastf1_client.get_race_state_rows(sess, state["actual_lap_used"])
        behind = next((r for r in rows if r["position"] == state["position"] + 1), None)
        if behind is None:
            traffic_risk, traffic_reason = "low", "No car immediately behind on track."
        else:
            gap_behind = behind["gap_to_ahead_s"]
            if gap_behind < pit_loss.pit_loss_time_s * 0.5:
                traffic_risk = "high"
                traffic_reason = (
                    f"{behind['driver']} is only {gap_behind:.1f}s behind — pitting risks "
                    "rejoining right in traffic with them."
                )
            elif gap_behind < pit_loss.pit_loss_time_s:
                traffic_risk = "medium"
                traffic_reason = (
                    f"{behind['driver']} is {gap_behind:.1f}s behind — should be a clean stop, "
                    "but with limited margin."
                )
            else:
                traffic_risk = "low"
                traffic_reason = f"{behind['driver']} is {gap_behind:.1f}s behind — plenty of margin for a clean stop."

        reasoning = (
            f"Comparing {state['tire_age_laps']}-lap-old {state['compound']} tires against a fresh "
            f"{fresh_compound} set with a {pit_loss.pit_loss_time_s:.1f}s pit loss ({pit_loss.source}), "
            f"the projected-optimal stop is lap {window.best_lap}, with laps "
            f"{window.start_lap}-{window.end_lap} within 1.5s of that optimum."
        )

        result = {
            "driver": driver,
            "current_lap": current_lap,
            "optimal_window": {"start_lap": window.start_lap, "end_lap": window.end_lap},
            "reasoning": reasoning,
            "traffic_risk": traffic_risk,
            "traffic_risk_reason": traffic_reason,
        }
        if current_model.is_low_confidence or fresh_model.is_low_confidence:
            result["warning"] = (
                "Degradation model confidence is low for at least one compound involved; "
                "treat this window as indicative only."
            )
        return result

    @server.tool()
    @_safe
    def simulate_undercut_overcut(
        own_driver: str,
        rival_driver: str,
        circuit: str,
        season: int,
        session: str,
        current_lap: int,
    ) -> dict[str, Any]:
        """Compares pitting `own_driver` before `rival_driver` (undercut)
        against staying out longer than them (overcut).
        """
        _validate_session_code(session)
        sess = fastf1_client.load_session(season, circuit, session)
        own_state = fastf1_client.get_driver_state_at_lap(sess, own_driver, current_lap)
        rival_state = fastf1_client.get_driver_state_at_lap(sess, rival_driver, current_lap)

        own_fresh_compound = strategy_sim.pick_alternative_compound(own_state["compound"])
        rival_fresh_compound = strategy_sim.pick_alternative_compound(rival_state["compound"])

        own_current_model = _get_degradation_model(circuit, season, own_state["compound"])
        own_fresh_model = _get_degradation_model(circuit, season, own_fresh_compound)
        rival_current_model = _get_degradation_model(circuit, season, rival_state["compound"])
        rival_fresh_model = _get_degradation_model(circuit, season, rival_fresh_compound)
        pit_loss = _get_pit_loss(circuit, season)

        initial_gap = fastf1_client.get_time_gap_between(sess, own_driver, rival_driver, current_lap)

        undercut, overcut = strategy_sim.simulate_undercut_overcut(
            own_current_model,
            own_fresh_model,
            own_state["tire_age_laps"],
            rival_current_model,
            rival_fresh_model,
            rival_state["tire_age_laps"],
            current_lap,
            pit_loss.pit_loss_time_s,
            initial_gap,
        )
        recommendation, reasoning = strategy_sim.recommend_undercut_or_overcut(undercut, overcut)

        return {
            "undercut": {
                "pit_lap": undercut.pit_lap,
                "projected_time_delta_s": undercut.projected_time_delta_s,
                "net_position_gain": undercut.net_position_gain,
            },
            "overcut": {
                "pit_lap": overcut.pit_lap,
                "projected_time_delta_s": overcut.projected_time_delta_s,
                "net_position_gain": overcut.net_position_gain,
            },
            "recommendation": recommendation,
            "reasoning": reasoning,
        }

    @server.tool()
    @_safe
    def compare_strategy_options(
        driver: str,
        circuit: str,
        season: int,
        session: str,
        current_lap: int,
        strategies: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Projects total remaining-race time for N hypothetical strategies
        (each: a label, a list of pit laps, and a list of compounds — one
        more compound than pit laps) for the same driver.
        """
        _validate_session_code(session)
        if not strategies:
            raise ToolError("`strategies` must contain at least one strategy to compare.")

        sess = fastf1_client.load_session(season, circuit, session)
        state = fastf1_client.get_driver_state_at_lap(sess, driver, current_lap)
        total_laps = fastf1_client.get_total_laps(sess)

        needed_compounds = {state["compound"]}
        for strat in strategies:
            needed_compounds.update(_validate_compound(c) for c in strat["compounds"])
        models_by_compound = {c: _get_degradation_model(circuit, season, c) for c in needed_compounds}

        results = []
        for strat in strategies:
            total_time = strategy_sim.simulate_full_strategy_time(
                models_by_compound,
                state["tire_age_laps"],
                current_lap,
                total_laps,
                strat["pit_laps"],
                [c.upper() for c in strat["compounds"]],
                _get_pit_loss(circuit, season).pit_loss_time_s,
            )
            results.append(
                {"label": strat["label"], "projected_total_time_s": round(total_time, 3)}
            )

        best_strategy = min(results, key=lambda r: r["projected_total_time_s"])["label"]
        return {"results": results, "best_strategy": best_strategy}

    @server.tool()
    @_safe
    def get_historical_strategies(
        circuit: str, seasons: list[int] | None = None, drivers: list[str] | None = None
    ) -> dict[str, Any]:
        """Real stint breakdown (compound, start/end lap) and finish position
        from past race sessions at this circuit.
        """
        target_seasons = seasons or [date.today().year - offset for offset in range(1, 4)]
        races = fastf1_client.get_historical_stints(circuit, target_seasons, drivers)
        if not races:
            raise ToolError(
                f"No historical race data found for circuit '{circuit}' in seasons {target_seasons}."
            )
        return {"circuit": circuit, "races": races}

    @server.tool()
    @_safe
    def predict_finish_position(
        driver: str,
        circuit: str,
        season: int,
        session: str,
        current_lap: int,
        planned_strategy: dict[str, Any],
    ) -> dict[str, Any]:
        """Projects `driver`'s finishing position and total time for a planned
        strategy, assuming rivals hold their recent pace with no further stops.
        """
        _validate_session_code(session)
        sess = fastf1_client.load_session(season, circuit, session)
        state = fastf1_client.get_driver_state_at_lap(sess, driver, current_lap)
        total_laps = fastf1_client.get_total_laps(sess)

        compounds = [_validate_compound(c) for c in planned_strategy["compounds"]]
        pit_laps = planned_strategy["pit_laps"]
        models_by_compound = {c: _get_degradation_model(circuit, season, c) for c in set(compounds)}

        own_remaining_time = strategy_sim.simulate_full_strategy_time(
            models_by_compound,
            state["tire_age_laps"],
            current_lap,
            total_laps,
            pit_laps,
            compounds,
            _get_pit_loss(circuit, season).pit_loss_time_s,
        )

        rows = fastf1_client.get_race_state_rows(sess, state["actual_lap_used"])
        remaining_laps = total_laps - current_lap
        own_gap_to_leader = next((r["gap_to_leader_s"] for r in rows if r["driver"] == driver), 0.0)
        projected_totals: dict[str, float] = {driver: own_gap_to_leader + own_remaining_time}
        for row in rows:
            other = row["driver"]
            if other == driver:
                continue
            pace = fastf1_client.get_recent_average_pace(sess, other, state["actual_lap_used"])
            if pace is None:
                continue
            projected_totals[other] = row["gap_to_leader_s"] + pace * remaining_laps

        ranking = sorted(projected_totals, key=lambda d: projected_totals[d])
        projected_finish_position = ranking.index(driver) + 1

        used_models = list(models_by_compound.values())
        avg_r2 = sum(m.r_squared for m in used_models) / len(used_models)
        any_fallback = any(m.data_source == "insufficient_data_fallback" for m in used_models)
        if any_fallback or avg_r2 < 0.3:
            confidence = "low"
        elif avg_r2 < 0.7:
            confidence = "medium"
        else:
            confidence = "high"

        return {
            "driver": driver,
            "projected_finish_position": projected_finish_position,
            "projected_total_time_s": round(own_remaining_time, 3),
            "confidence": confidence,
            "key_assumptions": [
                "Assumes rival drivers hold their recent (last 3-lap) pace for the rest of the race, with no further pit stops.",
                "Does not model safety cars, virtual safety cars, or race incidents.",
                "Does not model changing weather or track evolution.",
                "Assumes no on-track overtaking difficulty: a faster projected total time is assumed to translate directly into a better finishing position.",
            ],
        }

    @server.tool()
    def generate_strategy_report(
        race_context: dict[str, Any], decisions: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Formats a Markdown strategy report from an LLM/host-curated list of
        decisions. Purely deterministic formatting — no summarization here.
        """
        return report_builder.build_report(race_context, decisions)  # type: ignore[arg-type]
