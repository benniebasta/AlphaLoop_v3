from alphaloop.core.setup_types import normalize_pipeline_setup_type, normalize_schema_setup_type
from alphaloop.scoring.calibrator import SetupCalibrator


def test_normalize_pipeline_setup_type_maps_strategy_family_aliases():
    assert normalize_pipeline_setup_type("pullback_continuation") == "pullback"
    assert normalize_pipeline_setup_type("trend_continuation") == "continuation"
    assert normalize_pipeline_setup_type("range_reversal") == "reversal"
    assert normalize_pipeline_setup_type("momentum_expansion") == "continuation"
    assert normalize_pipeline_setup_type("range") == "range_bounce"


def test_calibrator_uses_canonical_setup_buckets_for_aliases():
    calibrator = SetupCalibrator(window=50)
    for _ in range(20):
        calibrator.record("momentum", True)

    assert calibrator.win_rate("continuation") == 1.0
    assert calibrator.calibration_factor("momentum_expansion") == 2.0


def test_normalize_schema_setup_type_maps_aliases_to_enum_safe_values():
    assert normalize_schema_setup_type("range_bounce") == "range"
    assert normalize_schema_setup_type("continuation") == "pullback"
    assert normalize_schema_setup_type("momentum_expansion") == "momentum"
