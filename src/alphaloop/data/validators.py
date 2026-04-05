"""
OHLC data integrity validation.

Validates fetched market data before it enters the trading pipeline.
Catches corrupt data, stale feeds, and data source quality issues.
"""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class OHLCValidationError(Exception):
    """Raised when OHLC data fails integrity checks."""


def validate_ohlcv(
    df: pd.DataFrame,
    symbol: str = "",
    *,
    strict: bool = False,
) -> tuple[bool, list[str]]:
    """
    Validate OHLCV DataFrame integrity.

    Returns (valid, list_of_issues).
    In strict mode, any issue makes the data invalid.
    In non-strict mode, only critical issues invalidate.

    Checks:
    1. Required columns exist
    2. No empty DataFrame
    3. High >= Low for all bars
    4. Open and Close within [Low, High]
    5. No negative prices
    6. No zero prices (unless market is legitimately at 0)
    7. No NaN in OHLC columns
    8. No duplicate timestamps
    9. Reasonable price changes (no 50%+ single-bar moves)
    10. Volume non-negative (if present)
    """
    issues: list[str] = []
    critical: list[str] = []

    required_cols = {"open", "high", "low", "close"}
    # Handle both cases (capitalized and lowercase)
    actual_cols = {c.lower() for c in df.columns}
    col_map = {c.lower(): c for c in df.columns}

    missing = required_cols - actual_cols
    if missing:
        critical.append(f"Missing required columns: {missing}")
        return False, critical

    if len(df) == 0:
        critical.append("Empty DataFrame")
        return False, critical

    o = df[col_map["open"]]
    h = df[col_map["high"]]
    l = df[col_map["low"]]  # noqa: E741
    c = df[col_map["close"]]

    # NaN check
    for name, series in [("open", o), ("high", h), ("low", l), ("close", c)]:
        nan_count = series.isna().sum()
        if nan_count > 0:
            pct = nan_count / len(df) * 100
            if pct > 10:
                critical.append(f"{name} has {nan_count} NaN values ({pct:.1f}%)")
            else:
                issues.append(f"{name} has {nan_count} NaN values ({pct:.1f}%)")

    # Drop NaN rows for further checks
    mask = o.notna() & h.notna() & l.notna() & c.notna()
    if mask.sum() == 0:
        critical.append("All rows have NaN values")
        return False, critical

    o_clean = o[mask]
    h_clean = h[mask]
    l_clean = l[mask]
    c_clean = c[mask]

    # Negative prices
    neg_count = ((o_clean < 0) | (h_clean < 0) | (l_clean < 0) | (c_clean < 0)).sum()
    if neg_count > 0:
        critical.append(f"{neg_count} bars have negative prices")

    # Zero prices (suspicious for most assets)
    zero_count = ((o_clean == 0) | (h_clean == 0) | (l_clean == 0) | (c_clean == 0)).sum()
    if zero_count > 0:
        issues.append(f"{zero_count} bars have zero prices")

    # High >= Low
    hl_violations = (h_clean < l_clean).sum()
    if hl_violations > 0:
        pct = hl_violations / len(df) * 100
        if pct > 5:
            critical.append(f"{hl_violations} bars have High < Low ({pct:.1f}%)")
        else:
            issues.append(f"{hl_violations} bars have High < Low ({pct:.1f}%)")

    # Open/Close within [Low, High]
    oc_violations = (
        (o_clean < l_clean) | (o_clean > h_clean) |
        (c_clean < l_clean) | (c_clean > h_clean)
    ).sum()
    if oc_violations > 0:
        pct = oc_violations / len(df) * 100
        if pct > 5:
            critical.append(
                f"{oc_violations} bars have Open/Close outside [Low,High] ({pct:.1f}%)"
            )
        else:
            issues.append(
                f"{oc_violations} bars have Open/Close outside [Low,High] ({pct:.1f}%)"
            )

    # Extreme single-bar moves (>50% from previous close)
    if len(c_clean) > 1:
        pct_changes = c_clean.pct_change().abs()
        extreme = (pct_changes > 0.50).sum()
        if extreme > 0:
            issues.append(f"{extreme} bars have >50% single-bar price change")

    # Duplicate timestamps
    if df.index.duplicated().any():
        dup_count = df.index.duplicated().sum()
        issues.append(f"{dup_count} duplicate timestamps")

    # Volume checks
    vol_col = col_map.get("volume")
    if vol_col and vol_col in df.columns:
        vol = df[vol_col]
        neg_vol = (vol < 0).sum()
        if neg_vol > 0:
            issues.append(f"{neg_vol} bars have negative volume")

    # Log results
    all_issues = critical + issues
    valid = len(critical) == 0
    if strict:
        valid = len(all_issues) == 0

    if all_issues:
        level = logging.ERROR if critical else logging.WARNING
        logger.log(
            level,
            "[ohlc-validate] %s: %d issues (%d critical) in %d bars",
            symbol, len(all_issues), len(critical), len(df),
        )
        for issue in all_issues:
            logger.log(level, "[ohlc-validate]   - %s", issue)

    return valid, all_issues


def detect_gaps(
    df: pd.DataFrame,
    expected_interval_minutes: int = 5,
    max_gap_multiple: float = 3.0,
) -> list[dict]:
    """
    Detect gaps in OHLCV time series.

    Returns list of gap descriptions.
    """
    if len(df) < 2:
        return []

    gaps = []
    expected_delta = pd.Timedelta(minutes=expected_interval_minutes)

    for i in range(1, len(df)):
        delta = df.index[i] - df.index[i - 1]
        if delta > expected_delta * max_gap_multiple:
            gaps.append({
                "from": str(df.index[i - 1]),
                "to": str(df.index[i]),
                "gap_minutes": delta.total_seconds() / 60,
                "expected_minutes": expected_interval_minutes,
                "multiple": delta / expected_delta,
            })

    if gaps:
        logger.info("[ohlc-validate] Found %d gaps in time series", len(gaps))

    return gaps
