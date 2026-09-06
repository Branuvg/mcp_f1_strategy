"""Strategy simulation engine: pit windows, undercut/overcut, arbitrary
strategy comparison and finish-position projection.

Pure math over `DegradationModel` instances and plain numbers — no MCP, no
FastF1. Every function that makes a simplifying assumption about the real
world (rivals hold constant pace, no safety car, etc.) says so in its
docstring; those assumptions are surfaced to the user via `key_assumptions`
in `predict_finish_position`'s tool output.
"""

from __future__ import annotations

from dataclasses import dataclass

from .degradation import DegradationModel

# Fixed short-term horizon used to evaluate undercut/overcut: long enough for
# the "extra" pit stop to happen and a few laps of clean/dirty air effect to
# play out, short enough that assuming the rival's pace stays constant over
# the window remains reasonable.
UNDERCUT_OVERCUT_HORIZON_LAPS = 6
OVERCUT_DELAY_LAPS = 3

# A time delta below this (seconds) is treated as noise, not a real edge.
NO_ADVANTAGE_THRESHOLD_S = 0.3


def project_stint_time(model: DegradationModel, start_age: int, num_laps: int) -> float:
    """Total time (s) for `num_laps` laps, tire age going start_age..start_age+num_laps-1."""
    if num_laps <= 0:
        return 0.0
    return sum(model.predict_lap_time(start_age + i) for i in range(num_laps))


def pick_alternative_compound(current_compound: str) -> str:
    """Default "other" compound used for a hypothetical fresh set when the
    caller doesn't specify one (pit window / undercut-overcut analysis).
    """
    return "MEDIUM" if current_compound == "HARD" else "HARD"


# ---------------------------------------------------------------------------
# Pit window
# ---------------------------------------------------------------------------


@dataclass
class PitWindowResult:
    start_lap: int
    end_lap: int
    best_lap: int
    best_total_time_s: float


def find_optimal_pit_window(
    current_compound_model: DegradationModel,
    fresh_compound_model: DegradationModel,
    current_tire_age: int,
    current_lap: int,
    total_laps: int,
    pit_loss_time_s: float,
    tolerance_s: float = 1.5,
) -> PitWindowResult:
    """For every candidate lap in (current_lap, total_laps), project the total
    remaining-race time if the driver pits on that lap (finishing the stint on
    `current_compound_model`, then a fresh set on `fresh_compound_model`).
    The window is every candidate within `tolerance_s` of the best one.
    """
    last_candidate = total_laps - 1  # leave at least one lap on the fresh tire
    first_candidate = current_lap + 1
    if first_candidate > last_candidate:
        return PitWindowResult(current_lap, current_lap, current_lap, 0.0)

    totals: dict[int, float] = {}
    for pit_lap in range(first_candidate, last_candidate + 1):
        laps_on_current = pit_lap - current_lap
        laps_on_fresh = total_laps - pit_lap
        totals[pit_lap] = (
            project_stint_time(current_compound_model, current_tire_age, laps_on_current)
            + pit_loss_time_s
            + project_stint_time(fresh_compound_model, 0, laps_on_fresh)
        )

    best_lap = min(totals, key=totals.get)
    best_time = totals[best_lap]
    within_tolerance = [lap for lap, t in totals.items() if t - best_time <= tolerance_s]
    return PitWindowResult(min(within_tolerance), max(within_tolerance), best_lap, best_time)


# ---------------------------------------------------------------------------
# Undercut / overcut
# ---------------------------------------------------------------------------


@dataclass
class ScenarioOutcome:
    pit_lap: int
    projected_time_delta_s: float
    net_position_gain: bool


def _simulate_one_stop(
    model_before: DegradationModel,
    tire_age_before: int,
    laps_before: int,
    pit_loss_time_s: float,
    model_after: DegradationModel,
    laps_after: int,
) -> float:
    total = project_stint_time(model_before, tire_age_before, laps_before)
    if laps_after > 0:
        total += pit_loss_time_s + project_stint_time(model_after, 0, laps_after)
    return total


def simulate_undercut_overcut(
    own_current_model: DegradationModel,
    own_fresh_model: DegradationModel,
    own_tire_age: int,
    rival_current_model: DegradationModel,
    rival_fresh_model: DegradationModel,
    rival_tire_age: int,
    current_lap: int,
    pit_loss_time_s: float,
    initial_gap_s: float,
    horizon_laps: int = UNDERCUT_OVERCUT_HORIZON_LAPS,
    overcut_delay_laps: int = OVERCUT_DELAY_LAPS,
) -> tuple[ScenarioOutcome, ScenarioOutcome]:
    """`initial_gap_s` = own_time - rival_time at current_lap (positive = own
    is behind rival by that many seconds).

    Undercut: own pits next lap, rival stays out for the whole horizon.
    Overcut: own stays out `overcut_delay_laps` extra laps, rival pits next lap.
    Both share the same horizon so the two scenarios are directly comparable.
    """

    def outcome(own_pit_lap: int, rival_pit_lap: int | None) -> ScenarioOutcome:
        own_laps_before = own_pit_lap - current_lap
        own_laps_after = horizon_laps - own_laps_before
        own_time = _simulate_one_stop(
            own_current_model, own_tire_age, own_laps_before,
            pit_loss_time_s, own_fresh_model, own_laps_after,
        )

        if rival_pit_lap is None:
            rival_time = project_stint_time(rival_current_model, rival_tire_age, horizon_laps)
        else:
            rival_laps_before = rival_pit_lap - current_lap
            rival_laps_after = horizon_laps - rival_laps_before
            rival_time = _simulate_one_stop(
                rival_current_model, rival_tire_age, rival_laps_before,
                pit_loss_time_s, rival_fresh_model, rival_laps_after,
            )

        time_delta = rival_time - own_time  # positive => own gains time on rival
        new_gap = initial_gap_s - time_delta  # positive => own still behind
        return ScenarioOutcome(own_pit_lap, round(time_delta, 3), new_gap < 0)

    undercut = outcome(own_pit_lap=current_lap + 1, rival_pit_lap=None)
    overcut = outcome(own_pit_lap=current_lap + overcut_delay_laps, rival_pit_lap=current_lap + 1)
    return undercut, overcut


def recommend_undercut_or_overcut(
    undercut: ScenarioOutcome, overcut: ScenarioOutcome
) -> tuple[str, str]:
    """Returns (recommendation, reasoning)."""
    best_delta = max(undercut.projected_time_delta_s, overcut.projected_time_delta_s)

    if best_delta < NO_ADVANTAGE_THRESHOLD_S:
        if undercut.projected_time_delta_s <= 0 and overcut.projected_time_delta_s <= 0:
            return (
                "stay_out",
                "Both an early stop (undercut) and an extended stint (overcut) project "
                "to lose time relative to the rival over the evaluation window; the best "
                "option is to stay on the current strategy for now.",
            )
        return (
            "no_clear_advantage",
            "The projected time gain from either an undercut "
            f"({undercut.projected_time_delta_s:+.2f}s) or an overcut "
            f"({overcut.projected_time_delta_s:+.2f}s) is within simulation noise "
            f"(< {NO_ADVANTAGE_THRESHOLD_S}s); neither is a clearly better call than the other.",
        )

    if undercut.projected_time_delta_s >= overcut.projected_time_delta_s:
        return (
            "undercut",
            f"Pitting now (lap {undercut.pit_lap}) projects to gain "
            f"{undercut.projected_time_delta_s:+.2f}s on the rival over the next "
            f"{UNDERCUT_OVERCUT_HORIZON_LAPS} laps by getting onto fresher tires before them"
            + (", which is enough to jump ahead of them on track." if undercut.net_position_gain else "."),
        )

    return (
        "overcut",
        f"Staying out and pitting later (lap {overcut.pit_lap}) projects to gain "
        f"{overcut.projected_time_delta_s:+.2f}s on the rival over the next "
        f"{UNDERCUT_OVERCUT_HORIZON_LAPS} laps by exploiting clean air while they pit first"
        + (", which is enough to come out ahead of them." if overcut.net_position_gain else "."),
    )


# ---------------------------------------------------------------------------
# Arbitrary strategy comparison / finish position projection
# ---------------------------------------------------------------------------


def simulate_full_strategy_time(
    models_by_compound: dict[str, DegradationModel],
    current_tire_age: int,
    current_lap: int,
    total_laps: int,
    pit_laps: list[int],
    compounds: list[str],
    pit_loss_time_s: float,
) -> float:
    """Total projected time (s) from `current_lap` to `total_laps` for a
    strategy described as `compounds[0]` (currently mounted tire, at its real
    current age) followed by a pit onto `compounds[1]` at `pit_laps[0]`, and
    so on. `len(compounds)` must be `len(pit_laps) + 1`.
    """
    if len(compounds) != len(pit_laps) + 1:
        raise ValueError("`compounds` must have exactly one more element than `pit_laps`.")

    boundaries = [current_lap, *pit_laps, total_laps]
    for a, b in zip(boundaries, boundaries[1:]):
        if b < a:
            raise ValueError("`pit_laps` must be increasing and within the remaining race.")

    total_time = 0.0
    for i, compound in enumerate(compounds):
        if compound not in models_by_compound:
            raise ValueError(f"No degradation model available for compound '{compound}'.")
        segment_laps = boundaries[i + 1] - boundaries[i]
        start_age = current_tire_age if i == 0 else 0
        total_time += project_stint_time(models_by_compound[compound], start_age, segment_laps)
        if i < len(pit_laps):
            total_time += pit_loss_time_s

    return total_time
