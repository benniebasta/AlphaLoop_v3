"""Tests for guard state persistence."""

import json
from collections import deque

from alphaloop.risk.guard_persistence import serialize_guards, restore_guards
from alphaloop.risk.guards import (
    ConfidenceVarianceFilter,
    DrawdownPauseGuard,
    EquityCurveScaler,
    SignalHashFilter,
    SpreadRegimeFilter,
)


def test_serialize_and_restore_hash_filter():
    hf = SignalHashFilter(window=3)
    hf._hashes = deque(["abc", "def", "ghi"], maxlen=3)

    json_str = serialize_guards(hash_filter=hf)
    data = json.loads(json_str)
    assert "hash_filter" in data
    assert data["hash_filter"]["hashes"] == ["abc", "def", "ghi"]

    # Restore into fresh guard
    hf2 = SignalHashFilter(window=3)
    restore_guards(json_str, hash_filter=hf2)
    assert list(hf2._hashes) == ["abc", "def", "ghi"]


def test_serialize_and_restore_confidence():
    cf = ConfidenceVarianceFilter(window=3, max_stdev=0.15)
    cf._confs = deque([0.80, 0.75, 0.85], maxlen=3)

    json_str = serialize_guards(conf_variance=cf)

    cf2 = ConfidenceVarianceFilter(window=3)
    restore_guards(json_str, conf_variance=cf2)
    assert list(cf2._confs) == [0.80, 0.75, 0.85]


def test_serialize_and_restore_equity_scaler():
    es = EquityCurveScaler(window=20)
    es._pnl = deque([10.0, -5.0, 15.0], maxlen=20)

    json_str = serialize_guards(equity_scaler=es)

    es2 = EquityCurveScaler(window=20)
    restore_guards(json_str, equity_scaler=es2)
    assert list(es2._pnl) == [10.0, -5.0, 15.0]


def test_serialize_and_restore_drawdown_pause():
    dp = DrawdownPauseGuard(pause_minutes=30)
    dp._recent["XAUUSD"].append((-10.0, 10.0))
    dp._recent["XAUUSD"].append((-15.0, 10.0))
    dp._recent["XAUUSD"].append((-20.0, 10.0))

    json_str = serialize_guards(dd_pause=dp)

    dp2 = DrawdownPauseGuard(pause_minutes=30)
    restore_guards(json_str, dd_pause=dp2)
    assert len(dp2._recent["XAUUSD"]) == 3
    assert dp2._recent["XAUUSD"][0] == (-10.0, 10.0)


def test_restore_invalid_json():
    hf = SignalHashFilter(window=3)
    # Should not crash on invalid JSON
    restore_guards("not valid json", hash_filter=hf)
    assert len(hf._hashes) == 0


def test_restore_empty_state():
    hf = SignalHashFilter(window=3)
    restore_guards("{}", hash_filter=hf)
    assert len(hf._hashes) == 0


def test_full_round_trip():
    """Test serializing all guards and restoring them."""
    hf = SignalHashFilter(window=3)
    hf._hashes = deque(["abc"], maxlen=3)
    cf = ConfidenceVarianceFilter(window=3)
    cf._confs = deque([0.8, 0.9], maxlen=3)
    sf = SpreadRegimeFilter(window=50)
    sf._spreads = deque([1.5, 2.0, 2.5], maxlen=50)
    es = EquityCurveScaler(window=20)
    es._pnl = deque([10.0], maxlen=20)
    dp = DrawdownPauseGuard(pause_minutes=30)
    dp._recent["XAUUSD"].append((-5.0, 5.0))

    json_str = serialize_guards(hf, cf, sf, es, dp)

    hf2 = SignalHashFilter(window=3)
    cf2 = ConfidenceVarianceFilter(window=3)
    sf2 = SpreadRegimeFilter(window=50)
    es2 = EquityCurveScaler(window=20)
    dp2 = DrawdownPauseGuard(pause_minutes=30)

    restore_guards(json_str, hf2, cf2, sf2, es2, dp2)
    assert list(hf2._hashes) == ["abc"]
    assert list(cf2._confs) == [0.8, 0.9]
    assert list(sf2._spreads) == [1.5, 2.0, 2.5]
    assert list(es2._pnl) == [10.0]
    assert len(dp2._recent["XAUUSD"]) == 1
