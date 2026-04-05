"""
pipeline/ — Institutional-grade 8-stage trading pipeline (v4).

Stages:
  1. MarketGate           — hard safety gates
  2. RegimeClassifier     — market state parameterization
  3. SignalGenerator       — direction + levels (delegated to signal engines)
  4A. StructuralInvalidation — setup-type-dependent hard/soft checks
  4B. StructuralQuality    — direction-dependent soft scoring
  5. ConvictionScorer      — weighted conviction with quality floors
  6. [AI Validator]        — bounded AI adjustments (algo_ai only)
  7. RiskGate             — portfolio risk capacity
  8. ExecutionGuard        — last-mile execution safety with delay mode
"""
