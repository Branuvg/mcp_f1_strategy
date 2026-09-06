from models.pit_loss import GENERIC_PIT_LOSS_S, estimate_pit_loss_time


def test_too_few_samples_falls_back_to_generic():
    estimate = estimate_pit_loss_time("Monza", samples=[21.0, 23.5])

    assert estimate.source == "generic_fallback"
    assert estimate.pit_loss_time_s == GENERIC_PIT_LOSS_S
    assert estimate.sample_size == 2


def test_enough_samples_uses_real_median():
    estimate = estimate_pit_loss_time("Monza", samples=[20.0, 22.0, 24.0, 100.0])

    assert estimate.source == "fastf1_real"
    # median is robust to the 100.0 outlier (e.g. a stop under a red flag)
    assert estimate.pit_loss_time_s == 23.0
    assert estimate.sample_size == 4
