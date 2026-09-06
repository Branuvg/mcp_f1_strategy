"""Tire degradation curve fitting.

Pure math: this module has no knowledge of MCP or FastF1. It receives
already-extracted (tire_age_laps, lap_time_s) samples and fits:

    lap_time(tire_age) = base_pace_s + degradation_rate_s_per_lap * tire_age ** k

with k = 1 (linear) or k = 2 (quadratic), picking whichever fits the real
samples better (see `fit_degradation_curve`).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Rough, widely-cited F1 degradation-rate ballpark figures per compound, used
# only when there isn't enough real data to fit a curve at all. These are not
# derived from data — `data_source` on the result always says so explicitly.
GENERIC_DEGRADATION_RATE_S_PER_LAP: dict[str, float] = {
    "SOFT": 0.09,
    "MEDIUM": 0.05,
    "HARD": 0.03,
    "INTERMEDIATE": 0.15,
    "WET": 0.20,
}
GENERIC_BASE_PACE_S = 90.0

MIN_SAMPLES_FOR_LINEAR_FIT = 3
MIN_SAMPLES_FOR_QUADRATIC_ATTEMPT = 5
QUADRATIC_R2_IMPROVEMENT_THRESHOLD = 0.02

# Below this, a fit is technically real but too weak to trust without saying so.
LOW_CONFIDENCE_R2 = 0.3
LOW_CONFIDENCE_SAMPLE_SIZE = 5


@dataclass
class DegradationModel:
    compound: str
    circuit: str
    base_pace_s: float
    degradation_rate_s_per_lap: float
    model_type: str  # "linear" | "quadratic"
    r_squared: float
    sample_size_laps: int
    data_source: str  # "fastf1_real" | "insufficient_data_fallback"

    def predict_lap_time(self, tire_age: int) -> float:
        k = 2 if self.model_type == "quadratic" else 1
        return self.base_pace_s + self.degradation_rate_s_per_lap * (tire_age**k)

    @property
    def is_low_confidence(self) -> bool:
        if self.data_source == "insufficient_data_fallback":
            return True
        return (
            self.r_squared < LOW_CONFIDENCE_R2
            or self.sample_size_laps < LOW_CONFIDENCE_SAMPLE_SIZE
        )


def _r_squared(y: np.ndarray, y_pred: np.ndarray) -> float:
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    if ss_tot == 0:
        return 1.0 if ss_res == 0 else 0.0
    return 1 - ss_res / ss_tot


def _fit_power_form(ages: np.ndarray, times: np.ndarray, k: int) -> tuple[float, float, float]:
    """Fit times = base + rate * ages**k by simple linear regression on the
    transformed feature ages**k. Returns (base_pace_s, rate, r_squared).
    """
    x = ages**k
    rate, base = np.polyfit(x, times, 1)
    predicted = base + rate * x
    return float(base), float(rate), _r_squared(times, predicted)


def fit_degradation_curve(
    compound: str,
    circuit: str,
    samples: list[tuple[int, float]],
    reference_pace_s: float | None = None,
) -> DegradationModel:
    """Fit a degradation curve from real (tire_age, lap_time_s) samples.

    Falls back to generic, clearly-labeled estimates when there are too few
    samples to fit anything meaningful (`data_source="insufficient_data_fallback"`).
    """
    n = len(samples)

    if n < MIN_SAMPLES_FOR_LINEAR_FIT:
        base_pace = (
            reference_pace_s
            if reference_pace_s is not None
            else (samples[0][1] if samples else GENERIC_BASE_PACE_S)
        )
        return DegradationModel(
            compound=compound,
            circuit=circuit,
            base_pace_s=round(base_pace, 3),
            degradation_rate_s_per_lap=GENERIC_DEGRADATION_RATE_S_PER_LAP.get(compound, 0.05),
            model_type="linear",
            r_squared=0.0,
            sample_size_laps=n,
            data_source="insufficient_data_fallback",
        )

    ages = np.array([s[0] for s in samples], dtype=float)
    times = np.array([s[1] for s in samples], dtype=float)

    base_pace, rate, r_squared = _fit_power_form(ages, times, 1)
    model_type = "linear"

    if n >= MIN_SAMPLES_FOR_QUADRATIC_ATTEMPT:
        quad_base, quad_rate, quad_r2 = _fit_power_form(ages, times, 2)
        if quad_r2 - r_squared > QUADRATIC_R2_IMPROVEMENT_THRESHOLD:
            base_pace, rate, r_squared = quad_base, quad_rate, quad_r2
            model_type = "quadratic"

    return DegradationModel(
        compound=compound,
        circuit=circuit,
        base_pace_s=round(base_pace, 3),
        degradation_rate_s_per_lap=round(rate, 4),
        model_type=model_type,
        r_squared=round(max(r_squared, 0.0), 4),
        sample_size_laps=n,
        data_source="fastf1_real",
    )
