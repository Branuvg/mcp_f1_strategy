"""Thin wrapper around FastF1: session loading, caching and raw data extraction.

This module is the only place in the project that imports `fastf1` or touches
`pandas` objects directly. Everything it returns to callers (`tools/`) is made
of plain Python types (str, int, float, list, dict) so that `models/` never
needs to know FastF1 exists.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import fastf1
import pandas as pd
from rapidfuzz import fuzz, process

VALID_SESSIONS = {"FP1", "FP2", "FP3", "Q", "R"}
VALID_COMPOUNDS = {"SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET"}

# Sessions checked (in order) when gathering laps for tire degradation
# calibration. The race and long-run practice sessions carry the most
# representative stint data; qualifying is intentionally excluded since laps
# there are single flying laps with fresh tires, not degradation stints.
DEGRADATION_SOURCE_SESSIONS = ["R", "FP2", "FP3", "FP1"]

# Minimum number of clean laps of a given compound we want before considering
# a degradation fit trustworthy enough to stop looking for more data.
MIN_SAMPLE_LAPS = 5


class DataNotAvailableError(Exception):
    """Raised when the base data required to answer a request does not exist.

    Maps 1:1 to the "explicit MCP error" case in the spec (section 7): the
    driver did not take part in the session, or the circuit/season/session
    combination has no data in FastF1 at all.
    """


# fastf1.get_session()/session.load() do real network + disk I/O, so sessions
# are cached in-process for the lifetime of the server: several tools may ask
# for the same circuit/season/session within one strategy conversation.
_session_cache: dict[tuple[int, str, str], fastf1.core.Session] = {}


def enable_cache(cache_dir: str | Path) -> None:
    path = Path(cache_dir)
    path.mkdir(parents=True, exist_ok=True)
    fastf1.Cache.enable_cache(str(path))


def load_session(season: int, circuit: str, session_code: str) -> fastf1.core.Session:
    """Load (and cache) a FastF1 session, with laps only (no telemetry/weather)."""
    key = (season, circuit, session_code)
    if key in _session_cache:
        return _session_cache[key]

    try:
        session = fastf1.get_session(season, circuit, session_code)
        session.load(telemetry=False, weather=False, messages=False)
    except Exception as exc:
        raise DataNotAvailableError(
            f"Could not load session '{session_code}' for circuit '{circuit}' "
            f"season {season} from FastF1: {exc}"
        ) from exc

    if session.laps is None or session.laps.empty:
        raise DataNotAvailableError(
            f"Session '{session_code}' for circuit '{circuit}' season {season} "
            "has no lap data available in FastF1."
        )

    _session_cache[key] = session
    return session


def try_load_session(season: int, circuit: str, session_code: str) -> fastf1.core.Session | None:
    """Same as load_session but returns None instead of raising.

    Used when probing supplementary sessions (e.g. FP2/FP3 for degradation
    calibration) where the absence of that session is not itself an error.
    """
    try:
        return load_session(season, circuit, session_code)
    except DataNotAvailableError:
        return None


def _lap_time_seconds(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if pd.isna(value):
        return None
    return float(value.total_seconds())


_NAME_MATCH_SCORE_CUTOFF = 75


def resolve_driver(session: fastf1.core.Session, driver: str) -> str:
    """Resolve a driver identifier to FastF1's 3-letter code for this session.

    An LLM caller is more likely to be given a driver's name by the user than
    their FastF1 code, so this accepts (case-insensitive): an exact code
    ("LEC"), a car number ("16"), a last or full name ("Leclerc", "Charles
    Leclerc"), or — as a last resort — a fuzzy match on full name to tolerate
    minor typos, the same way FastF1 itself fuzzy-matches circuit names.
    """
    query = driver.strip()
    canonical = query.upper()

    results = session.results
    if results is None or results.empty:
        known_codes = sorted(session.laps["Driver"].dropna().unique().tolist())
        if canonical in known_codes:
            return canonical
        raise DataNotAvailableError(
            f"Driver '{driver}' not recognized in this session. Known driver codes: {known_codes}."
        )

    known_codes = set(results["Abbreviation"].dropna().unique())
    if canonical in known_codes:
        return canonical

    by_number = results[results["DriverNumber"].astype(str) == query]
    if not by_number.empty:
        return str(by_number.iloc[0]["Abbreviation"])

    lower_query = query.lower()
    for _, row in results.iterrows():
        if lower_query in {str(row["LastName"]).lower(), str(row["FullName"]).lower()}:
            return str(row["Abbreviation"])

    full_names_by_code = {
        str(row["Abbreviation"]): str(row["FullName"]) for _, row in results.iterrows()
    }
    match = process.extractOne(
        query, full_names_by_code.values(), scorer=fuzz.WRatio, score_cutoff=_NAME_MATCH_SCORE_CUTOFF
    )
    if match is not None:
        matched_name = match[0]
        return next(code for code, name in full_names_by_code.items() if name == matched_name)

    raise DataNotAvailableError(
        f"Driver '{driver}' not recognized in this session. "
        f"Known drivers: {sorted(full_names_by_code.values())}."
    )


def get_driver_laps(session: fastf1.core.Session, driver: str) -> pd.DataFrame:
    laps = session.laps.pick_drivers(driver)
    if laps.empty:
        raise DataNotAvailableError(
            f"Driver '{driver}' has no recorded laps in this session."
        )
    return laps


def get_race_state_rows(session: fastf1.core.Session, lap: int) -> list[dict[str, Any]]:
    """Positions, gaps and tire info for every driver at a given lap number."""
    laps = session.laps
    lap_rows = laps[laps["LapNumber"] == lap].dropna(subset=["Position"])
    if lap_rows.empty:
        raise DataNotAvailableError(f"No data available for lap {lap} in this session.")

    lap_rows = lap_rows.sort_values("Position")
    leader_time = lap_rows.iloc[0]["Time"]

    rows: list[dict[str, Any]] = []
    prev_time = None
    for _, row in lap_rows.iterrows():
        gap_to_leader = (row["Time"] - leader_time).total_seconds()
        gap_to_ahead = 0.0 if prev_time is None else (row["Time"] - prev_time).total_seconds()
        rows.append(
            {
                "driver": row["Driver"],
                "position": int(row["Position"]),
                "gap_to_leader_s": round(gap_to_leader, 3),
                "gap_to_ahead_s": round(gap_to_ahead, 3),
                "compound": row["Compound"] if pd.notna(row["Compound"]) else "UNKNOWN",
                "tire_age_laps": int(row["TyreLife"]) if pd.notna(row["TyreLife"]) else 0,
            }
        )
        prev_time = row["Time"]
    return rows


def get_driver_state_at_lap(
    session: fastf1.core.Session, driver: str, lap: int
) -> dict[str, Any]:
    """Single-driver slice of `get_race_state_rows`, with a couple of "not run
    that far yet" fallbacks: if the exact lap is missing (e.g. retired driver,
    or the query lap is ahead of what's recorded), use the closest lap at or
    before it.
    """
    row = _row_at_or_before_lap(session, driver, lap)
    all_rows = get_race_state_rows(session, int(row["LapNumber"]))
    own = next((r for r in all_rows if r["driver"] == driver), None)
    if own is None:
        raise DataNotAvailableError(
            f"Driver '{driver}' could not be matched into the classification at lap {lap}."
        )
    own["actual_lap_used"] = int(row["LapNumber"])
    return own


def _row_at_or_before_lap(session: fastf1.core.Session, driver: str, lap: int) -> pd.Series:
    driver_laps = get_driver_laps(session, driver).dropna(subset=["LapNumber"])
    candidates = driver_laps[driver_laps["LapNumber"] <= lap]
    if candidates.empty:
        candidates = driver_laps
    if candidates.empty:
        raise DataNotAvailableError(f"Driver '{driver}' has no usable lap data near lap {lap}.")
    return candidates.sort_values("LapNumber").iloc[-1]


def get_time_gap_between(
    session: fastf1.core.Session, driver_a: str, driver_b: str, lap: int
) -> float:
    """Elapsed-session-time gap between two drivers near `lap`: driver_a's
    time minus driver_b's time (positive = driver_a is behind driver_b).

    Approximate for drivers not on the exact same lap count at that point in
    the race (e.g. one of them already lapped) — acceptable for the tactical,
    short-horizon undercut/overcut comparison this feeds into.
    """
    row_a = _row_at_or_before_lap(session, driver_a, lap)
    row_b = _row_at_or_before_lap(session, driver_b, lap)
    return float((row_a["Time"] - row_b["Time"]).total_seconds())


def get_clean_compound_laps(
    session: fastf1.core.Session, compound: str
) -> list[tuple[int, float]]:
    """(tire_age, lap_time_s) pairs for laps considered representative of pure
    tire pace: not in/out laps, and flagged "accurate" by FastF1 (excludes
    laps affected by traffic, yellow flags, safety car, etc).
    """
    laps = session.laps.pick_compounds(compound).pick_wo_box().pick_accurate()
    pairs: list[tuple[int, float]] = []
    for _, row in laps.iterrows():
        lap_time = _lap_time_seconds(row["LapTime"])
        tire_age = row["TyreLife"]
        if lap_time is None or pd.isna(tire_age):
            continue
        pairs.append((int(tire_age), lap_time))
    return pairs


def gather_degradation_samples(
    circuit: str, season: int, compound: str, max_seasons_back: int = 2
) -> tuple[list[tuple[int, float]], float | None]:
    """Collect (tire_age, lap_time_s) samples for compound+circuit across the
    sessions of the requested event, and — if still thin — the same race
    session of previous seasons at the same circuit.

    Returns the sample pairs plus a reference "fastest lap of the event"
    (seconds, any compound) to support a circuit-aware fallback base pace when
    there is no real data for this exact compound.
    """
    samples: list[tuple[int, float]] = []
    reference_fastest: float | None = None

    for session_code in DEGRADATION_SOURCE_SESSIONS:
        session = try_load_session(season, circuit, session_code)
        if session is None:
            continue
        if reference_fastest is None:
            fastest = _lap_time_seconds(session.laps["LapTime"].min())
            reference_fastest = fastest
        samples.extend(get_clean_compound_laps(session, compound))
        if len(samples) >= MIN_SAMPLE_LAPS:
            break

    seasons_checked = 0
    year = season - 1
    while len(samples) < MIN_SAMPLE_LAPS and seasons_checked < max_seasons_back:
        session = try_load_session(year, circuit, "R")
        if session is not None:
            if reference_fastest is None:
                reference_fastest = _lap_time_seconds(session.laps["LapTime"].min())
            samples.extend(get_clean_compound_laps(session, compound))
        seasons_checked += 1
        year -= 1

    return samples, reference_fastest


def gather_pit_stop_samples(session: fastf1.core.Session) -> list[float]:
    """Time lost (seconds) on each in-lap+out-lap pit stop pair, relative to
    the average pace of the surrounding green-flag laps of the same driver.

    Used to estimate a circuit-specific pit loss time from real data.
    """
    laps = session.laps
    losses: list[float] = []

    for driver in laps["Driver"].unique():
        driver_laps = laps.pick_drivers(driver).sort_values("LapNumber")
        clean = driver_laps.pick_wo_box().pick_accurate()
        if clean.empty:
            continue
        reference_pace = clean["LapTime"].apply(_lap_time_seconds).dropna()
        if reference_pace.empty:
            continue
        reference_pace_s = float(reference_pace.median())

        in_laps = driver_laps[driver_laps["PitInTime"].notna()]
        for _, in_row in in_laps.iterrows():
            in_time = _lap_time_seconds(in_row["LapTime"])
            out_candidates = driver_laps[driver_laps["LapNumber"] == in_row["LapNumber"] + 1]
            if in_time is None or out_candidates.empty:
                continue
            out_time = _lap_time_seconds(out_candidates.iloc[0]["LapTime"])
            if out_time is None:
                continue
            extra_time = (in_time - reference_pace_s) + (out_time - reference_pace_s)
            if extra_time > 0:
                losses.append(extra_time)

    return losses


def get_total_laps(session: fastf1.core.Session) -> int:
    total = getattr(session, "total_laps", None)
    if total is not None and not (isinstance(total, float) and math.isnan(total)):
        return int(total)
    return int(session.laps["LapNumber"].max())


def get_recent_average_pace(
    session: fastf1.core.Session, driver: str, up_to_lap: int, window: int = 3
) -> float | None:
    """Average clean lap time (s) for a driver over the `window` laps up to
    and including `up_to_lap`. Used as a simple "current pace" proxy for
    rivals in `predict_finish_position`, under the explicit assumption that
    they keep this pace for the rest of the race.
    """
    laps = get_driver_laps(session, driver)
    recent = laps[laps["LapNumber"] <= up_to_lap].pick_wo_box().pick_accurate()
    recent = recent.sort_values("LapNumber").tail(window)
    if recent.empty:
        return None
    times = [t for t in recent["LapTime"].apply(_lap_time_seconds) if t is not None]
    if not times:
        return None
    return sum(times) / len(times)


def get_historical_stints(
    circuit: str, seasons: list[int], drivers: list[str] | None
) -> list[dict[str, Any]]:
    """Per-driver stint breakdown (compound, start/end lap) and finish position
    for race sessions of the given circuit/seasons.
    """
    races: list[dict[str, Any]] = []
    for season in seasons:
        session = try_load_session(season, circuit, "R")
        if session is None:
            continue

        laps = session.laps
        driver_codes = drivers if drivers else sorted(laps["Driver"].dropna().unique().tolist())

        for driver in driver_codes:
            driver_laps = laps.pick_drivers(driver).sort_values("LapNumber")
            if driver_laps.empty:
                continue

            stints: list[dict[str, Any]] = []
            for stint_number in sorted(driver_laps["Stint"].dropna().unique()):
                stint_laps = driver_laps[driver_laps["Stint"] == stint_number]
                compound = stint_laps["Compound"].iloc[0]
                stints.append(
                    {
                        "compound": compound if pd.notna(compound) else "UNKNOWN",
                        "start_lap": int(stint_laps["LapNumber"].min()),
                        "end_lap": int(stint_laps["LapNumber"].max()),
                    }
                )

            finish_position = driver_laps["Position"].dropna()
            if finish_position.empty:
                continue

            races.append(
                {
                    "season": season,
                    "driver": driver,
                    "stints": stints,
                    "finish_position": int(finish_position.iloc[-1]),
                }
            )

    return races
