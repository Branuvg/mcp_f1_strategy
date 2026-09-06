from models.degradation import (
    GENERIC_DEGRADATION_RATE_S_PER_LAP,
    fit_degradation_curve,
)


def test_insufficient_samples_falls_back_to_generic():
    model = fit_degradation_curve("SOFT", "Monza", samples=[(1, 90.0), (2, 90.5)])

    assert model.data_source == "insufficient_data_fallback"
    assert model.sample_size_laps == 2
    assert model.degradation_rate_s_per_lap == GENERIC_DEGRADATION_RATE_S_PER_LAP["SOFT"]
    assert model.r_squared == 0.0


def test_no_samples_uses_reference_pace_as_base():
    model = fit_degradation_curve("HARD", "Monza", samples=[], reference_pace_s=82.0)

    assert model.data_source == "insufficient_data_fallback"
    assert model.base_pace_s == 82.0


def test_perfect_linear_fit_is_recovered_exactly():
    base, rate = 90.0, 0.1
    samples = [(age, base + rate * age) for age in range(1, 10)]

    model = fit_degradation_curve("MEDIUM", "Monza", samples)

    assert model.data_source == "fastf1_real"
    assert model.model_type == "linear"
    assert model.base_pace_s == round(base, 3)
    assert model.degradation_rate_s_per_lap == round(rate, 4)
    assert model.r_squared == 1.0
    assert not model.is_low_confidence


def test_quadratic_signal_is_picked_over_linear_when_it_fits_much_better():
    base, rate = 88.0, 0.02
    ages = list(range(1, 12))
    samples = [(age, base + rate * age**2) for age in ages]

    model = fit_degradation_curve("SOFT", "Silverstone", samples)

    assert model.model_type == "quadratic"
    assert model.r_squared > 0.99
    assert abs(model.predict_lap_time(5) - (base + rate * 25)) < 0.01


def test_low_r_squared_is_flagged_as_low_confidence():
    # Same lap time regardless of tire age: no real relationship for the
    # model to find, so r_squared should collapse towards 0.
    samples = [(age, 90.0) for age in range(1, 8)]

    model = fit_degradation_curve("MEDIUM", "Spa", samples)

    assert model.data_source == "fastf1_real"
    assert model.is_low_confidence
