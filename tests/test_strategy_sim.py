import pytest

from models import strategy_sim
from models.degradation import DegradationModel


def make_model(compound: str, base: float, rate: float, model_type: str = "linear") -> DegradationModel:
    return DegradationModel(
        compound=compound,
        circuit="TestCircuit",
        base_pace_s=base,
        degradation_rate_s_per_lap=rate,
        model_type=model_type,
        r_squared=0.9,
        sample_size_laps=20,
        data_source="fastf1_real",
    )


def test_project_stint_time_sums_predicted_laps():
    model = make_model("MEDIUM", base=90.0, rate=0.1)
    # ages 5,6,7 -> 90.5 + 90.6 + 90.7
    total = strategy_sim.project_stint_time(model, start_age=5, num_laps=3)
    assert total == pytest.approx(90.5 + 90.6 + 90.7)


def test_project_stint_time_zero_laps_is_zero():
    model = make_model("MEDIUM", base=90.0, rate=0.1)
    assert strategy_sim.project_stint_time(model, start_age=0, num_laps=0) == 0.0


def test_pick_alternative_compound_avoids_current():
    assert strategy_sim.pick_alternative_compound("HARD") == "MEDIUM"
    assert strategy_sim.pick_alternative_compound("MEDIUM") == "HARD"
    assert strategy_sim.pick_alternative_compound("SOFT") == "HARD"


def test_find_optimal_pit_window_prefers_pitting_when_current_tire_degrades_fast():
    # Current tire degrades heavily; fresh tire is flat -> pitting ASAP should win.
    worn = make_model("SOFT", base=90.0, rate=1.0)
    fresh = make_model("HARD", base=91.0, rate=0.01)

    window = strategy_sim.find_optimal_pit_window(
        current_compound_model=worn,
        fresh_compound_model=fresh,
        current_tire_age=20,
        current_lap=20,
        total_laps=40,
        pit_loss_time_s=20.0,
    )

    assert window.best_lap == 21


def test_find_optimal_pit_window_prefers_staying_out_when_current_tire_is_fine():
    flat = make_model("HARD", base=90.0, rate=0.01)
    fresh = make_model("MEDIUM", base=90.0, rate=0.01)

    window = strategy_sim.find_optimal_pit_window(
        current_compound_model=flat,
        fresh_compound_model=fresh,
        current_tire_age=5,
        current_lap=20,
        total_laps=40,
        pit_loss_time_s=20.0,
    )

    # With near-zero degradation on both sides, exactly when you pit barely
    # matters: the whole remaining range should fall within tolerance.
    assert window.start_lap == 21
    assert window.end_lap == 39


def test_simulate_undercut_overcut_rewards_undercut_when_rival_tire_is_much_older():
    own_current = make_model("MEDIUM", base=90.0, rate=0.05)
    own_fresh = make_model("HARD", base=90.0, rate=0.02)
    rival_current = make_model("MEDIUM", base=90.0, rate=0.5)  # heavily worn
    rival_fresh = make_model("HARD", base=90.0, rate=0.02)

    undercut, overcut = strategy_sim.simulate_undercut_overcut(
        own_current, own_fresh, own_tire_age=18,
        rival_current_model=rival_current, rival_fresh_model=rival_fresh, rival_tire_age=18,
        current_lap=20, pit_loss_time_s=20.0, initial_gap_s=2.0,
    )

    assert undercut.projected_time_delta_s > 0
    recommendation, _ = strategy_sim.recommend_undercut_or_overcut(undercut, overcut)
    assert recommendation in {"undercut", "overcut"}


def test_simulate_full_strategy_time_matches_manual_calculation():
    models = {
        "MEDIUM": make_model("MEDIUM", base=90.0, rate=0.0),
        "HARD": make_model("HARD", base=91.0, rate=0.0),
    }
    # current_lap=10, tire_age=5, pit at lap 20, total_laps=30
    total = strategy_sim.simulate_full_strategy_time(
        models, current_tire_age=5, current_lap=10, total_laps=30,
        pit_laps=[20], compounds=["MEDIUM", "HARD"], pit_loss_time_s=22.0,
    )
    # 10 laps @ 90.0 + 22.0 pit loss + 10 laps @ 91.0
    assert total == pytest.approx(10 * 90.0 + 22.0 + 10 * 91.0)


def test_simulate_full_strategy_time_rejects_mismatched_lengths():
    models = {"MEDIUM": make_model("MEDIUM", base=90.0, rate=0.0)}
    with pytest.raises(ValueError):
        strategy_sim.simulate_full_strategy_time(
            models, current_tire_age=0, current_lap=0, total_laps=10,
            pit_laps=[5], compounds=["MEDIUM"], pit_loss_time_s=20.0,
        )
