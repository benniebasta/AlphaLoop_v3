from alphaloop.monitoring.alert_rules import AlertEngine, AlertRule, AlertSeverity, create_default_rules

def test_register_and_evaluate():
    engine = AlertEngine()
    rule = AlertRule(
        name="test_rule",
        description="Test",
        condition=lambda ctx: ctx.get("value", 0) > 10,
    )
    engine.register_rule(rule)
    alerts = engine.evaluate({"value": 5})
    assert len(alerts) == 0
    alerts = engine.evaluate({"value": 15})
    assert len(alerts) == 1
    assert alerts[0].rule_name == "test_rule"

def test_cooldown():
    engine = AlertEngine()
    rule = AlertRule(name="cd", description="Cooldown test", cooldown_seconds=9999, condition=lambda ctx: True)
    engine.register_rule(rule)
    alerts1 = engine.evaluate({})
    assert len(alerts1) == 1
    alerts2 = engine.evaluate({})
    assert len(alerts2) == 0  # cooldown blocks

def test_disabled_rule():
    engine = AlertEngine()
    rule = AlertRule(name="off", description="Disabled", enabled=False, condition=lambda ctx: True)
    engine.register_rule(rule)
    assert len(engine.evaluate({})) == 0

def test_default_rules():
    rules = create_default_rules()
    assert len(rules) == 5
    assert any(r.name == "daily_loss_limit" for r in rules)

def test_get_active_alerts():
    engine = AlertEngine()
    engine.register_rule(AlertRule(name="a", description="A", condition=lambda ctx: True))
    engine.evaluate({})
    active = engine.get_active_alerts()
    assert len(active) == 1

def test_acknowledge():
    engine = AlertEngine()
    engine.register_rule(AlertRule(name="a", description="A", condition=lambda ctx: True))
    engine.evaluate({})
    engine.acknowledge(0)
    active = engine.get_active_alerts()
    assert len(active) == 0
