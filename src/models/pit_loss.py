"""Pit loss time estimation: pure math over already-extracted samples."""

from __future__ import annotations

import statistics
from dataclasses import dataclass

# ~20-25s is the typical total time lost (in-lap + pit lane + out-lap, minus
# what a green-flag lap would have taken) at most modern F1 circuits.
GENERIC_PIT_LOSS_S = 22.5
MIN_SAMPLES_FOR_REAL_ESTIMATE = 3


@dataclass
class PitLossEstimate:
    circuit: str
    pit_loss_time_s: float
    source: str  # "fastf1_real" | "generic_fallback"
    sample_size: int


def estimate_pit_loss_time(circuit: str, samples: list[float]) -> PitLossEstimate:
    if len(samples) < MIN_SAMPLES_FOR_REAL_ESTIMATE:
        return PitLossEstimate(
            circuit=circuit,
            pit_loss_time_s=GENERIC_PIT_LOSS_S,
            source="generic_fallback",
            sample_size=len(samples),
        )
    return PitLossEstimate(
        circuit=circuit,
        pit_loss_time_s=round(statistics.median(samples), 3),
        source="fastf1_real",
        sample_size=len(samples),
    )
