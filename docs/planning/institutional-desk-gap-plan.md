# AlphaLoop vs Institutional Desk

## Current gaps

- Pre-trade portfolio approval existed, but the early cross-instance check ran before final sizing and therefore did not include the projected risk of the trade about to be sent.
- Live execution could continue after order-intent persistence failed, which leaves a reconciliation hole after crashes or broker/network ambiguity.
- Daily risk windows were mixed between local-machine date handling and UTC runtime handling, so daily loss controls could drift around midnight.
- Emergency heartbeat escalation published an invalid `RiskLimitHit` payload, which meant the dead-man-switch could lose its own telemetry.

## Institutional-desk baseline

- Pre-trade checks must be performed on the actual sized order, not just on existing exposure.
- Every order needs an immutable intent record before broker submission.
- Risk windows, reconciliation boundaries, and audit timestamps should share one canonical clock.
- Emergency controls must emit valid operational telemetry because desk supervision depends on it.

## Implemented now

- Added `execution/control_plane.py` to provide projected-risk approval plus order-intent journaling ahead of v4 broker submission.
- Wired the v4 execution path through that control plane so live trading fails closed if journaling is unavailable.
- Normalized daily-risk aggregation to UTC day boundaries.
- Fixed dead-man-switch risk-event publishing and added regression coverage.

## Next upgrades to reach a stronger desk-grade posture

- Make the durable order journal the single source of truth for all execution modes, not only the v4 path.
- Reconcile on deterministic `client_order_id` as well as broker ticket to survive partial broker acknowledgements and reconnects.
- Replace the default SQLite deployment path with PostgreSQL plus explicit migrations in every non-dev runtime.
- Add post-trade broker fill reconciliation SLAs, venue-quality metrics, and operator ack workflows for emergency states.
