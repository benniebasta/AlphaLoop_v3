from alphaloop.core.feature_flags import FeatureFlags

def test_default_flags():
    ff = FeatureFlags()
    assert ff.is_enabled("websocket_events") is True
    assert ff.is_enabled("metaloop_enabled") is False

def test_override():
    ff = FeatureFlags()
    assert ff.is_enabled("debug_logging") is False
    ff.set_override("debug_logging", True)
    assert ff.is_enabled("debug_logging") is True
    ff.clear_override("debug_logging")
    assert ff.is_enabled("debug_logging") is False

def test_unknown_flag():
    ff = FeatureFlags()
    assert ff.is_enabled("nonexistent") is False

def test_get_all():
    ff = FeatureFlags()
    all_flags = ff.get_all()
    assert "websocket_events" in all_flags
    assert all_flags["websocket_events"]["current"] is True

def test_contains():
    ff = FeatureFlags()
    assert ("websocket_events" in ff) is True
    assert ("metaloop_enabled" in ff) is False
