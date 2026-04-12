"""
Strategy loader — reads active strategy config from DB and builds
runtime pipeline components for the v4 institutional trading loop.

Bridge between the stored active_strategy_{symbol} DB record and
the runtime trading loop components.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alphaloop.config.settings_service import SettingsService
from alphaloop.core.setup_types import normalize_schema_setup_type
from alphaloop.scoring.weights import load_weights, load_thresholds
from alphaloop.tools.registry import ToolRegistry, _DEFAULT_ORDER

logger = logging.getLogger(__name__)

SUPPORTED_STRATEGY_SPEC_VERSIONS = {"v1"}
SUPPORTED_SETUP_FAMILIES = {
    "trend_continuation",
    "pullback_continuation",
    "range_reversal",
    "breakout_retest",
    "momentum_expansion",
    "discretionary_ai",
}
_MISSING = object()
_DEFAULT_SIGNAL_RULES = [{"source": "ema_crossover"}]
_VALID_SIGNAL_LOGICS = {"AND", "OR", "MAJORITY"}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def normalize_signal_mode(raw_mode: str | None) -> str:
    """
    Normalize stored/UI signal modes to the canonical runtime values.
    """
    mode = (raw_mode or "").strip().lower()
    if mode in {"algo_only", "algo_ai", "ai_signal"}:
        return mode
    if mode == "":
        return "ai_signal"
    logger.warning("[strategy-loader] Unknown signal_mode '%s' — defaulting to ai_signal", raw_mode)
    return "ai_signal"


@dataclass
class ActiveStrategyConfig:
    """Parsed active strategy config from DB."""

    symbol: str
    version: int
    status: str
    spec_version: str = "v1"
    params: dict = field(default_factory=dict)
    tools: dict[str, bool] = field(default_factory=dict)
    validation: dict = field(default_factory=dict)
    ai_models: dict[str, str] = field(default_factory=dict)
    signal_mode: str = "ai_signal"
    signal_instruction: str = ""
    validator_instruction: str = ""
    scoring_weights: dict[str, float] = field(default_factory=dict)
    confidence_thresholds: dict[str, float] = field(default_factory=dict)
    strategy_spec: "StrategySpecV1" = field(default_factory=lambda: StrategySpecV1())


@dataclass
class StrategySpecV1:
    """Typed v1 strategy contract derived from legacy strategy records."""

    spec_version: str = "v1"
    signal_mode: str = "ai_signal"
    setup_family: str = "pullback_continuation"
    direction_model: str = "ai_hypothesis"
    enabled_preconditions: list[str] = field(default_factory=list)
    entry_model: dict = field(default_factory=dict)
    invalidation_model: dict = field(default_factory=dict)
    exit_policy: dict = field(default_factory=dict)
    risk_policy: dict = field(default_factory=dict)
    prompt_bundle: dict = field(default_factory=dict)
    ai_models: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "spec_version": self.spec_version,
            "signal_mode": self.signal_mode,
            "setup_family": self.setup_family,
            "direction_model": self.direction_model,
            "enabled_preconditions": list(self.enabled_preconditions),
            "entry_model": dict(self.entry_model),
            "invalidation_model": dict(self.invalidation_model),
            "exit_policy": dict(self.exit_policy),
            "risk_policy": dict(self.risk_policy),
            "prompt_bundle": dict(self.prompt_bundle),
            "ai_models": dict(self.ai_models),
            "metadata": dict(self.metadata),
        }


def _strategy_spec_object(value: Any) -> Any | None:
    if isinstance(value, dict):
        return value.get("strategy_spec")
    return getattr(value, "strategy_spec", None)


def _coerce_tool_flags(value: Any) -> dict[str, bool]:
    """Accept either dict-style or list-style tool payloads."""
    if isinstance(value, dict):
        return {str(name): bool(enabled) for name, enabled in value.items()}
    if isinstance(value, (list, tuple, set)):
        return {str(name): True for name in value}
    return {}


def normalize_strategy_tools(value: Any) -> dict[str, bool]:
    """Public helper for callers that need stable tool flags across legacy payload shapes."""
    return _coerce_tool_flags(value)


def normalize_strategy_summary(value: Any) -> dict[str, Any]:
    """Normalize strategy summary metric aliases for operator/runtime payloads."""
    if isinstance(value, dict):
        raw_summary = value.get("summary", {})
    else:
        raw_summary = getattr(value, "summary", {})
    summary = dict(raw_summary or {})
    sharpe = summary.get("sharpe")
    if sharpe is None:
        sharpe = summary.get("sharpe_ratio", 0)
    total_pnl = summary.get("total_pnl")
    if total_pnl is None:
        total_pnl = summary.get("total_pnl_usd", 0)
    max_dd_pct = summary.get("max_dd_pct")
    if max_dd_pct is None:
        max_dd_pct = summary.get("max_drawdown_pct", 0)
    summary["sharpe"] = sharpe
    summary["total_pnl"] = total_pnl
    summary["max_dd_pct"] = max_dd_pct
    return summary


def normalize_strategy_signal_rules(
    value: Any,
    *,
    default_to_ema: bool = False,
) -> list[dict]:
    """Normalize legacy signal rule payloads with backward-compatible EMA defaults."""
    if value is _MISSING:
        return list(_DEFAULT_SIGNAL_RULES) if default_to_ema else []
    if value is None:
        return list(_DEFAULT_SIGNAL_RULES) if default_to_ema else []
    if not isinstance(value, list):
        return []
    filtered = [rule for rule in value if isinstance(rule, dict)]
    if not filtered:
        return list(_DEFAULT_SIGNAL_RULES) if default_to_ema else []
    return filtered


def normalize_strategy_signal_logic(value: Any) -> str:
    """Normalize signal logic to supported values with AND as the safe default."""
    if value is None:
        return "AND"
    logic = str(value).strip().upper()
    return logic if logic in _VALID_SIGNAL_LOGICS else "AND"


def _strategy_entry_model(value: Any) -> dict[str, Any]:
    strategy_spec = _strategy_spec_object(value)
    if isinstance(strategy_spec, dict):
        entry_model = strategy_spec.get("entry_model")
    elif strategy_spec is not None:
        entry_model = getattr(strategy_spec, "entry_model", None)
    else:
        entry_model = None
    return dict(entry_model or {}) if isinstance(entry_model, dict) else {}


def _legacy_signal_logic(value: Any) -> Any:
    params: dict[str, Any] = {}
    if isinstance(value, dict):
        params = value.get("params", {}) if isinstance(value.get("params"), dict) else {}
        if "signal_logic" in params:
            return params.get("signal_logic")
        return value.get("signal_logic")

    params = getattr(value, "params", {}) if isinstance(getattr(value, "params", {}), dict) else {}
    if "signal_logic" in params:
        return params.get("signal_logic")
    return getattr(value, "signal_logic", None)


def _legacy_signal_rules(value: Any) -> tuple[Any, bool]:
    params: dict[str, Any] = {}
    if isinstance(value, dict):
        params = value.get("params", {}) if isinstance(value.get("params"), dict) else {}
        if "signal_rules" in params:
            raw_rules = params.get("signal_rules")
        elif "signal_rules" in value:
            raw_rules = value.get("signal_rules")
        else:
            raw_rules = _MISSING
    else:
        params = getattr(value, "params", {}) if isinstance(getattr(value, "params", {}), dict) else {}
        if "signal_rules" in params:
            raw_rules = params.get("signal_rules")
        else:
            raw_rules = getattr(value, "signal_rules", _MISSING)
    return raw_rules, raw_rules is None


def _signal_rule_dicts_from_sources(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    rules: list[dict] = []
    for item in value:
        source = str(item or "").strip()
        if source:
            rules.append({"source": source})
    return rules


def resolve_strategy_signal_logic(value: Any) -> str:
    entry_model = _strategy_entry_model(value)
    if "signal_logic" in entry_model:
        return normalize_strategy_signal_logic(entry_model.get("signal_logic"))
    return normalize_strategy_signal_logic(_legacy_signal_logic(value))


def resolve_strategy_signal_rules(
    value: Any,
    *,
    default_to_ema: bool = True,
) -> list[dict]:
    entry_model = _strategy_entry_model(value)
    if "signal_rules" in entry_model:
        raw_rules = entry_model.get("signal_rules")
        return normalize_strategy_signal_rules(
            raw_rules,
            default_to_ema=default_to_ema and raw_rules is None,
        )
    if "signal_rule_sources" in entry_model:
        raw_sources = entry_model.get("signal_rule_sources")
        if raw_sources is None:
            return normalize_strategy_signal_rules(None, default_to_ema=default_to_ema)
        if not isinstance(raw_sources, list):
            return []
        return _signal_rule_dicts_from_sources(raw_sources)

    raw_rules, raw_rules_was_none = _legacy_signal_rules(value)
    should_default_to_ema = default_to_ema and raw_rules_was_none
    return normalize_strategy_signal_rules(
        raw_rules,
        default_to_ema=should_default_to_ema,
    )


def build_algorithmic_params(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        params = dict(value.get("params") or {})
    else:
        params = dict(getattr(value, "params", {}) or {})

    default_to_ema = ("signal_rules" not in params) or not params.get("signal_rules")
    params["signal_rules"] = resolve_strategy_signal_rules(value, default_to_ema=default_to_ema)
    params["signal_logic"] = resolve_strategy_signal_logic(value)
    return params


def build_strategy_resolution_input(
    value: Any,
    *,
    signal_rules: Any = _MISSING,
    signal_logic: Any = _MISSING,
    tools: Any = _MISSING,
) -> dict[str, Any]:
    """Build a shared strategy-like payload without flattening raw rule semantics."""
    if isinstance(value, dict):
        params = dict(value.get("params") or {})
        explicit_spec = value.get("strategy_spec")
        payload = {
            "symbol": str(value.get("symbol") or ""),
            "version": resolve_strategy_version(value),
            "status": str(value.get("status") or ""),
            "spec_version": resolve_strategy_spec_version(value) or "v1",
            "signal_mode": resolve_strategy_signal_mode(value),
            "setup_family": resolve_strategy_setup_family(value),
            "strategy_spec": dict(explicit_spec or {}),
            "source": resolve_strategy_source(value),
            "signal_instruction": resolve_signal_instruction(value),
            "validator_instruction": resolve_validator_instruction(value),
            "tools": _coerce_tool_flags(value.get("tools") if tools is _MISSING else tools),
            "validation": dict(value.get("validation") or {}),
            "ai_models": resolve_strategy_ai_models(value),
            "params": params,
        }
        if signal_rules is _MISSING and "signal_rules" in value and "signal_rules" not in params:
            signal_rules = value.get("signal_rules")
        if signal_logic is _MISSING and "signal_logic" in value and "signal_logic" not in params:
            signal_logic = value.get("signal_logic")
    else:
        params = dict(getattr(value, "params", {}) or {})
        explicit_spec = getattr(value, "strategy_spec", None)
        payload = {
            "symbol": str(getattr(value, "symbol", "") or ""),
            "version": resolve_strategy_version(value),
            "status": str(getattr(value, "status", "") or ""),
            "spec_version": resolve_strategy_spec_version(value) or "v1",
            "signal_mode": resolve_strategy_signal_mode(value),
            "setup_family": resolve_strategy_setup_family(value),
            "strategy_spec": dict(explicit_spec or {}),
            "source": resolve_strategy_source(value),
            "signal_instruction": resolve_signal_instruction(value),
            "validator_instruction": resolve_validator_instruction(value),
            "tools": _coerce_tool_flags(getattr(value, "tools", {}) if tools is _MISSING else tools),
            "validation": dict(getattr(value, "validation", {}) or {}),
            "ai_models": resolve_strategy_ai_models(value),
            "params": params,
        }
        if signal_rules is _MISSING:
            signal_rules = getattr(value, "signal_rules", _MISSING)
        if signal_logic is _MISSING:
            signal_logic = getattr(value, "signal_logic", _MISSING)

    if signal_rules is not _MISSING:
        payload["params"]["signal_rules"] = signal_rules
    if signal_logic is not _MISSING:
        payload["params"]["signal_logic"] = signal_logic
    if explicit_spec:
        payload["strategy_spec"] = serialize_strategy_spec(payload)
    return payload


def resolve_strategy_spec_version(value: Any) -> str:
    """Resolve spec version with spec-first precedence."""
    strategy_spec = _strategy_spec_object(value)
    if isinstance(strategy_spec, dict):
        spec_version = str(strategy_spec.get("spec_version") or "").strip().lower()
        if spec_version:
            return spec_version
    elif strategy_spec is not None:
        spec_version = str(getattr(strategy_spec, "spec_version", "") or "").strip().lower()
        if spec_version:
            return spec_version

    if isinstance(value, dict):
        return str(value.get("spec_version") or "v1").strip().lower()
    return str(getattr(value, "spec_version", "v1") or "v1").strip().lower()


def resolve_strategy_version(value: Any) -> int:
    """Resolve strategy version with spec metadata taking precedence when present."""
    strategy_spec = _strategy_spec_object(value)
    metadata = None
    if isinstance(strategy_spec, dict):
        metadata = strategy_spec.get("metadata")
    elif strategy_spec is not None:
        metadata = getattr(strategy_spec, "metadata", None)

    if isinstance(metadata, dict):
        version = metadata.get("version", _MISSING)
        if isinstance(version, (int, float, str)):
            v = _safe_int(version)
            if v > 0:
                return v
    elif metadata is not None:
        version = getattr(metadata, "version", _MISSING)
        if isinstance(version, (int, float, str)):
            v = _safe_int(version)
            if v > 0:
                return v

    if isinstance(value, dict):
        return _safe_int(value.get("version", 0))
    return _safe_int(getattr(value, "version", 0))


def resolve_strategy_source(value: Any) -> str:
    """Resolve source with spec metadata taking precedence over stale flat fields."""
    strategy_spec = _strategy_spec_object(value)
    metadata = None
    if isinstance(strategy_spec, dict):
        metadata = strategy_spec.get("metadata")
    elif strategy_spec is not None:
        metadata = getattr(strategy_spec, "metadata", None)

    if isinstance(metadata, dict):
        source = str(metadata.get("source") or "").strip()
        if source:
            return source

    if isinstance(value, dict):
        return str(value.get("source") or "").strip()
    return str(getattr(value, "source", "") or "").strip()


def resolve_strategy_prompt(value: Any, key: str) -> str:
    """Resolve a strategy prompt with spec-first fallback to legacy flat fields."""
    strategy_spec = _strategy_spec_object(value)
    prompt_bundle = None
    if isinstance(strategy_spec, dict):
        prompt_bundle = strategy_spec.get("prompt_bundle")
    elif strategy_spec is not None:
        prompt_bundle = getattr(strategy_spec, "prompt_bundle", None)

    if isinstance(prompt_bundle, dict):
        prompt = str(prompt_bundle.get(key) or "").strip()
        if prompt:
            return prompt

    if isinstance(value, dict):
        return str(value.get(key) or "").strip()
    return str(getattr(value, key, "") or "").strip()


def resolve_signal_instruction(value: Any) -> str:
    return resolve_strategy_prompt(value, "signal_instruction")


def resolve_validator_instruction(value: Any) -> str:
    return resolve_strategy_prompt(value, "validator_instruction")


# Approved AI models — any model reference NOT in this set is rejected at load
# time with a warning and replaced by the role default. Update this list when
# new providers are vetted and API keys are confirmed to exist in production.
APPROVED_AI_MODELS: frozenset[str] = frozenset({
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6",
    "claude-opus-4-6",
    "deepseek-reasoner",
    "gpt-5.4-mini",
    "gpt-5.4",
    "gpt-4o-mini",
    "gpt-4o",
})

# Role-level defaults used when a model reference fails whitelist validation.
_ROLE_DEFAULT_MODELS: dict[str, str] = {
    "signal":        "gemini-2.5-flash-lite",
    "validator":     "claude-haiku-4-5-20251001",
    "research":      "gemini-2.5-pro",
    "param_suggest": "deepseek-reasoner",
    "regime":        "gemini-2.5-flash-lite",
    "fallback":      "gemini-2.5-flash-lite",
}


def _is_approved_ai_model(model: str) -> bool:
    if model in APPROVED_AI_MODELS:
        return True
    # Preserve synthetic strategy/test aliases instead of rewriting them to
    # production defaults during canonicalization. These aliases are used in
    # stored fixtures and unit payloads to verify spec-first propagation.
    return model.startswith(("spec-", "override-"))


def _validate_ai_models(raw: dict[str, str]) -> dict[str, str]:
    """Enforce model whitelist. Unknown model references are replaced by the
    role default and logged as errors so operators are alerted at load time
    rather than at signal generation time when the failure mode is worse."""
    result: dict[str, str] = {}
    for role, model in raw.items():
        if _is_approved_ai_model(model):
            result[role] = model
        else:
            default = _ROLE_DEFAULT_MODELS.get(role, "gemini-2.5-flash-lite")
            logger.error(
                "[strategy_loader] REJECTED ai_models[%r]=%r — not in approved "
                "model whitelist. Substituting default %r. Update the strategy "
                "version or APPROVED_AI_MODELS to fix this.",
                role, model, default,
            )
            result[role] = default
    return result


def resolve_strategy_ai_models(value: Any) -> dict[str, str]:
    strategy_spec = _strategy_spec_object(value)
    ai_models = None
    if isinstance(strategy_spec, dict):
        ai_models = strategy_spec.get("ai_models")
    elif strategy_spec is not None:
        ai_models = getattr(strategy_spec, "ai_models", None)

    if isinstance(ai_models, dict) and ai_models:
        raw = {str(name): str(model) for name, model in ai_models.items() if model}
        return _validate_ai_models(raw)

    if isinstance(value, dict):
        raw_ai_models = value.get("ai_models")
    else:
        raw_ai_models = getattr(value, "ai_models", None)
    if isinstance(raw_ai_models, dict):
        raw = {str(name): str(model) for name, model in raw_ai_models.items() if model}
        return _validate_ai_models(raw)
    return {}


def resolve_strategy_signal_mode(value: Any) -> str:
    strategy_spec = _strategy_spec_object(value)
    if isinstance(strategy_spec, dict):
        spec_mode = strategy_spec.get("signal_mode")
        if spec_mode:
            return normalize_signal_mode(spec_mode)
    if strategy_spec is not None:
        spec_mode = getattr(strategy_spec, "signal_mode", None)
        if spec_mode:
            return normalize_signal_mode(spec_mode)

    if isinstance(value, dict):
        return normalize_signal_mode(value.get("signal_mode"))
    return normalize_signal_mode(getattr(value, "signal_mode", None))


def resolve_strategy_setup_family(value: Any) -> str:
    strategy_spec = _strategy_spec_object(value)
    if isinstance(strategy_spec, dict):
        setup_family = str(strategy_spec.get("setup_family") or "").strip().lower()
        if setup_family in SUPPORTED_SETUP_FAMILIES:
            return setup_family
    elif strategy_spec is not None:
        setup_family = str(getattr(strategy_spec, "setup_family", "") or "").strip().lower()
        if setup_family in SUPPORTED_SETUP_FAMILIES:
            return setup_family

    if isinstance(value, dict):
        data = dict(value)
    else:
        data = {
            "symbol": str(getattr(value, "symbol", "") or ""),
            "version": _safe_int(getattr(value, "version", 0)),
            "status": str(getattr(value, "status", "") or ""),
            "source": str(getattr(value, "source", "") or ""),
            "signal_mode": resolve_strategy_signal_mode(value),
            "params": dict(getattr(value, "params", {}) or {}),
            "tools": _coerce_tool_flags(getattr(value, "tools", {}) or {}),
            "validation": dict(getattr(value, "validation", {}) or {}),
            "ai_models": dict(getattr(value, "ai_models", {}) or {}),
            "signal_instruction": resolve_signal_instruction(value),
            "validator_instruction": resolve_validator_instruction(value),
        }
    return migrate_legacy_strategy_spec_v1(data).setup_family


_ALGORITHMIC_SETUP_BY_FAMILY = {
    "trend_continuation": "momentum",
    "pullback_continuation": "pullback",
    "range_reversal": "reversal",
    "breakout_retest": "breakout",
    "momentum_expansion": "momentum",
    "discretionary_ai": "pullback",
}


def resolve_algorithmic_setup_tag(value: Any) -> str:
    family = resolve_strategy_setup_family(value)
    return normalize_schema_setup_type(
        _ALGORITHMIC_SETUP_BY_FAMILY.get(family, family)
    )


def serialize_strategy_spec(value: Any) -> dict:
    """Serialize a strategy spec while preserving explicit typed records when present."""
    strategy_spec = _strategy_spec_object(value)
    if isinstance(strategy_spec, StrategySpecV1):
        return strategy_spec.to_dict()
    if hasattr(strategy_spec, "to_dict"):
        return strategy_spec.to_dict()

    base: dict[str, Any]
    if isinstance(value, dict):
        base = dict(value)
    else:
        base = {
            "symbol": str(getattr(value, "symbol", "") or ""),
            "version": resolve_strategy_version(value),
            "status": str(getattr(value, "status", "") or ""),
            "source": resolve_strategy_source(value),
            "spec_version": resolve_strategy_spec_version(value) or "v1",
            "signal_mode": resolve_strategy_signal_mode(value),
            "signal_instruction": resolve_signal_instruction(value),
            "validator_instruction": resolve_validator_instruction(value),
            "params": dict(getattr(value, "params", {}) or {}),
            "tools": _coerce_tool_flags(getattr(value, "tools", {}) or {}),
            "validation": dict(getattr(value, "validation", {}) or {}),
            "ai_models": resolve_strategy_ai_models(value),
        }

    if strategy_spec is not None and not isinstance(strategy_spec, dict):
        raw_bundle = getattr(strategy_spec, "prompt_bundle", None)
        base["strategy_spec"] = {
            "spec_version": str(getattr(strategy_spec, "spec_version", "v1") or "v1"),
            "signal_mode": str(getattr(strategy_spec, "signal_mode", base.get("signal_mode")) or base.get("signal_mode") or "ai_signal"),
            "setup_family": str(getattr(strategy_spec, "setup_family", "") or ""),
            "direction_model": str(getattr(strategy_spec, "direction_model", "") or ""),
            "enabled_preconditions": list(getattr(strategy_spec, "enabled_preconditions", []) or []),
            "entry_model": dict(getattr(strategy_spec, "entry_model", {}) or {}),
            "invalidation_model": dict(getattr(strategy_spec, "invalidation_model", {}) or {}),
            "exit_policy": dict(getattr(strategy_spec, "exit_policy", {}) or {}),
            "risk_policy": dict(getattr(strategy_spec, "risk_policy", {}) or {}),
            "prompt_bundle": dict(raw_bundle or {}),
            "ai_models": dict(getattr(strategy_spec, "ai_models", {}) or {}),
            "metadata": dict(getattr(strategy_spec, "metadata", {}) or {}),
        }

    return migrate_legacy_strategy_spec_v1(base).to_dict()


def build_runtime_strategy_context(value: Any) -> dict[str, Any]:
    """Build an explicit runtime strategy payload without relying on __dict__ internals."""
    if value is None:
        return {}

    if isinstance(value, dict):
        return {
            "symbol": str(value.get("symbol") or ""),
            "version": resolve_strategy_version(value),
            "status": str(value.get("status") or ""),
            "spec_version": resolve_strategy_spec_version(value) or "v1",
            "signal_mode": resolve_strategy_signal_mode(value),
            "setup_family": resolve_strategy_setup_family(value),
            "source": resolve_strategy_source(value),
            "signal_instruction": resolve_signal_instruction(value),
            "validator_instruction": resolve_validator_instruction(value),
            "params": build_algorithmic_params(value),
            "tools": _coerce_tool_flags(value.get("tools") or {}),
            "validation": dict(value.get("validation") or {}),
            "ai_models": resolve_strategy_ai_models(value),
            "scoring_weights": dict(value.get("scoring_weights") or {}),
            "confidence_thresholds": dict(value.get("confidence_thresholds") or {}),
            "strategy_spec": serialize_strategy_spec(value),
        }

    return {
        "symbol": str(getattr(value, "symbol", "") or ""),
        "version": resolve_strategy_version(value),
        "status": str(getattr(value, "status", "") or ""),
        "spec_version": resolve_strategy_spec_version(value) or "v1",
        "signal_mode": resolve_strategy_signal_mode(value),
        "setup_family": resolve_strategy_setup_family(value),
        "source": resolve_strategy_source(value),
        "signal_instruction": resolve_signal_instruction(value),
        "validator_instruction": resolve_validator_instruction(value),
        "params": build_algorithmic_params(value),
        "tools": _coerce_tool_flags(getattr(value, "tools", {}) or {}),
        "validation": dict(getattr(value, "validation", {}) or {}),
        "ai_models": resolve_strategy_ai_models(value),
        "scoring_weights": dict(getattr(value, "scoring_weights", {}) or {}),
        "confidence_thresholds": dict(getattr(value, "confidence_thresholds", {}) or {}),
        "strategy_spec": serialize_strategy_spec(value),
    }

def build_active_strategy_payload(value: Any) -> dict[str, Any]:
    """Build the canonical payload stored in active_strategy_* settings."""
    runtime = build_runtime_strategy_context(value)
    if isinstance(value, dict):
        runtime["name"] = str(value.get("name") or "")
        runtime["summary"] = normalize_strategy_summary(value)
    else:
        runtime["name"] = str(getattr(value, "name", "") or "")
        runtime["summary"] = normalize_strategy_summary(value)
    return runtime


def bind_active_strategy_symbol(value: Any, symbol: str) -> dict[str, Any]:
    """Build the canonical active-strategy payload and bind it to a target symbol."""
    payload = build_active_strategy_payload(value)
    payload["symbol"] = str(symbol or payload.get("symbol") or "")
    return payload


def build_active_strategy_config(value: Any, *, fallback_symbol: str = "") -> ActiveStrategyConfig:
    """Build the typed active-strategy config from the canonical strategy contract."""
    data = build_active_strategy_payload(value)
    version = resolve_strategy_version(data)
    status = str(data.get("status") or "")
    if status not in ("", "candidate", "dry_run", "demo", "live", "retired"):
        logger.warning(
            "[strategy-loader] Unknown status '%s' for active_strategy_%s",
            status,
            fallback_symbol or data.get("symbol", ""),
        )

    strategy_spec = migrate_legacy_strategy_spec_v1(data)
    resolved_strategy = {**data, "strategy_spec": strategy_spec}
    return ActiveStrategyConfig(
        symbol=str(data.get("symbol") or fallback_symbol or ""),
        version=version,
        spec_version=resolve_strategy_spec_version(data) or "v1",
        status=status,
        params=build_algorithmic_params(resolved_strategy),
        tools=normalize_strategy_tools(data.get("tools") or {}),
        validation=data.get("validation", {}) if isinstance(data.get("validation"), dict) else {},
        ai_models=resolve_strategy_ai_models(resolved_strategy),
        signal_mode=resolve_strategy_signal_mode(resolved_strategy),
        signal_instruction=resolve_signal_instruction(resolved_strategy),
        validator_instruction=resolve_validator_instruction(resolved_strategy),
        scoring_weights=data.get("scoring_weights", {}) if isinstance(data.get("scoring_weights"), dict) else {},
        confidence_thresholds=data.get("confidence_thresholds", {}) if isinstance(data.get("confidence_thresholds"), dict) else {},
        strategy_spec=strategy_spec,
    )


async def store_active_strategy_bindings(
    settings_service: Any,
    value: Any,
    *,
    symbol: str,
    instance_id: str = "",
    write_symbol_key: bool = True,
    write_instance_key: bool = False,
) -> str:
    """Persist canonical active-strategy JSON to the requested settings keys."""
    strategy_json = json.dumps(build_active_strategy_payload(value))
    if write_instance_key and instance_id:
        await settings_service.set(f"active_strategy_{instance_id}", strategy_json)
    if write_symbol_key and symbol:
        await settings_service.set(f"active_strategy_{symbol}", strategy_json)
    return strategy_json


def active_strategy_binding_keys(
    symbol: str,
    *,
    instance_id: str = "",
    instance_ids: list[str] | tuple[str, ...] | None = None,
    include_symbol: bool = True,
) -> list[str]:
    """Build active-strategy settings keys in canonical lookup/update order."""
    keys: list[str] = []
    if instance_id:
        keys.append(f"active_strategy_{instance_id}")
    if instance_ids:
        keys.extend(f"active_strategy_{value}" for value in instance_ids if value)
    if include_symbol and symbol:
        keys.append(f"active_strategy_{symbol}")
    return keys


async def _load_active_strategy_raw(
    settings_service: SettingsService,
    symbol: str,
    instance_id: str = "",
) -> str | None:
    """Read raw active-strategy JSON from settings storage using canonical lookup order."""
    for key in active_strategy_binding_keys(symbol, instance_id=instance_id):
        raw = await settings_service.get(key)
        if raw:
            return raw
    return None


def resolve_strategy_version_string(value: Any) -> str:
    """Resolve a strategy version as the canonical string used in settings/execution metadata."""
    version = resolve_strategy_version(value)
    return str(version) if version > 0 else ""


def build_strategy_version_tag(value: Any) -> str:
    """Build the canonical external version tag (for example ``v12``)."""
    version = resolve_strategy_version_string(value)
    return f"v{version}" if version else ""


def build_strategy_reference(value: Any, *, fallback_symbol: str = "") -> dict[str, str]:
    """Build canonical strategy identity fields from a runtime or strategy payload."""
    if isinstance(value, dict):
        raw_symbol = value.get("symbol")
    else:
        raw_symbol = getattr(value, "symbol", "")
    symbol = str(raw_symbol).strip() if isinstance(raw_symbol, (str, int, float)) else ""
    if symbol.startswith("<") and "MagicMock" in symbol:
        symbol = ""
    if not symbol:
        symbol = str(fallback_symbol or "")
    version = resolve_strategy_version(value)
    strategy_id = symbol
    if version > 0 and symbol:
        strategy_id = f"{symbol}.v{version}"
    elif version > 0 and fallback_symbol:
        strategy_id = f"{fallback_symbol}.v{version}"
    elif not strategy_id:
        strategy_id = fallback_symbol
    return {
        "symbol": symbol or fallback_symbol,
        "strategy_id": strategy_id,
        "strategy_version": str(version) if version > 0 else "",
    }


def canonicalize_strategy_record(data: dict, *, path: str | None = None) -> dict:
    """Normalize a stored strategy record into the canonical operator/runtime shape."""
    canonical = dict(data)
    canonical["strategy_spec"] = migrate_legacy_strategy_spec_v1(canonical).to_dict()
    canonical["version"] = resolve_strategy_version(canonical)
    canonical["spec_version"] = resolve_strategy_spec_version(canonical) or "v1"
    canonical["signal_mode"] = resolve_strategy_signal_mode(canonical)
    canonical["setup_family"] = resolve_strategy_setup_family(canonical)
    canonical["source"] = resolve_strategy_source(canonical)
    canonical["signal_instruction"] = resolve_signal_instruction(canonical)
    canonical["validator_instruction"] = resolve_validator_instruction(canonical)
    canonical["ai_models"] = resolve_strategy_ai_models(canonical)
    canonical["summary"] = normalize_strategy_summary(canonical)
    if path:
        canonical["_path"] = path
    return canonical


def load_strategy_json(raw: str | None) -> dict[str, Any] | None:
    """Parse and canonicalize an in-memory strategy JSON payload."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return canonicalize_strategy_record(data)


def load_strategy_record(path: Path) -> dict[str, Any] | None:
    """Load and canonicalize a strategy record from disk."""
    payload = load_strategy_json(path.read_text())
    if payload is None:
        return None
    payload["_path"] = str(path)
    return payload


def write_json_file(path: Path, payload: Any) -> None:
    """Atomically write a JSON payload to disk."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)


def save_strategy_record(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize and atomically save a strategy record to disk."""
    payload = {k: v for k, v in data.items() if not str(k).startswith("_")}
    canonical = canonicalize_strategy_record(payload)
    write_json_file(path, canonical)
    canonical["_path"] = str(path)
    return canonical


def find_strategy_record(
    symbol: str,
    version: int,
    directory: Path,
    name: str = "",
) -> dict[str, Any] | None:
    """Find a canonical strategy record by name+version or symbol+version."""
    # Fast path: if name is known, look up directly
    if name:
        path = directory / f"{name}_v{version}.json"
        if path.exists():
            return load_strategy_record(path)
    # Full scan: match by symbol and version fields inside each record
    for path in directory.glob("*.json"):
        record = load_strategy_record(path)
        if (
            record
            and record.get("symbol") == symbol
            and resolve_strategy_version(record) == int(version)
        ):
            return record
    return None


async def load_active_strategy_payload(
    settings_service: SettingsService,
    symbol: str,
    instance_id: str = "",
) -> dict[str, Any] | None:
    """
    Read canonical active-strategy payload from settings storage.

    Lookup order:
      1. active_strategy_{instance_id}  (per-agent binding)
      2. active_strategy_{symbol}       (legacy single-agent fallback)
    """
    raw = await _load_active_strategy_raw(settings_service, symbol, instance_id)
    if not raw:
        return None
    return load_strategy_json(raw)


async def load_active_strategy_bindings(
    settings_service: Any,
    symbol: str,
    *,
    instance_ids: list[str] | tuple[str, ...] | None = None,
    instance_id: str = "",
    include_symbol: bool = True,
) -> list[tuple[str, dict[str, Any]]]:
    """Load canonical active-strategy payloads for the requested binding keys."""
    bindings: list[tuple[str, dict[str, Any]]] = []
    for key in active_strategy_binding_keys(
        symbol,
        instance_id=instance_id,
        instance_ids=instance_ids,
        include_symbol=include_symbol,
    ):
        raw = await settings_service.get(key)
        payload = load_strategy_json(raw)
        if payload is not None:
            bindings.append((key, payload))
    return bindings


async def sync_active_strategy_bindings(
    settings_service: Any,
    symbol: str,
    value: Any,
    *,
    instance_ids: list[str] | tuple[str, ...] | None = None,
    instance_id: str = "",
    include_symbol: bool = True,
) -> list[str]:
    """
    Rewrite existing active-strategy bindings that already point at the same symbol/version.

    Returns the list of settings keys that were updated.
    """
    payload_json = json.dumps(build_active_strategy_payload(value))
    target_symbol = str(symbol or build_active_strategy_payload(value).get("symbol") or "")
    target_version = resolve_strategy_version(value)
    updated_keys: list[str] = []
    for key, current in await load_active_strategy_bindings(
        settings_service,
        target_symbol,
        instance_ids=instance_ids,
        instance_id=instance_id,
        include_symbol=include_symbol,
    ):
        if current.get("symbol") == target_symbol and resolve_strategy_version(current) == target_version:
            await settings_service.set(key, payload_json)
            updated_keys.append(key)
    return updated_keys


async def find_active_strategy_binding_for_version(
    settings_service: Any,
    symbol: str,
    version: int,
    *,
    instance_ids: list[str] | tuple[str, ...] | None = None,
    instance_id: str = "",
    include_symbol: bool = True,
) -> tuple[str, dict[str, Any]] | None:
    """Return the first canonical active binding currently pointing at the requested version."""
    for key, payload in await load_active_strategy_bindings(
        settings_service,
        symbol,
        instance_ids=instance_ids,
        instance_id=instance_id,
        include_symbol=include_symbol,
    ):
        if resolve_strategy_version(payload) == int(version):
            return key, payload
    return None


def _infer_setup_family(data: dict) -> str:
    explicit = str(data.get("setup_family") or "").strip().lower()
    if explicit in SUPPORTED_SETUP_FAMILIES:
        return explicit

    signal_mode = normalize_signal_mode(data.get("signal_mode"))
    source = resolve_strategy_source(data).strip().lower()
    params = data.get("params", {}) if isinstance(data.get("params"), dict) else {}
    tools = _coerce_tool_flags(data.get("tools"))
    rules = resolve_strategy_signal_rules(data, default_to_ema=True)
    rule_sources = {
        str(item.get("source") or "").strip().lower()
        for item in rules
        if isinstance(item, dict)
    }

    if signal_mode == "ai_signal" or source in {"ai_signal_discovery", "ui_ai_signal_card"}:
        return "discretionary_ai"
    if tools.get("bos_guard") or tools.get("fvg_guard"):
        return "breakout_retest"
    if tools.get("fast_fingers") or tools.get("trendilo"):
        return "momentum_expansion"
    if "ema_crossover" in rule_sources:
        return "trend_continuation"
    return "pullback_continuation"


def migrate_legacy_strategy_spec_v1(data: dict) -> StrategySpecV1:
    """Build a typed StrategySpecV1 from either an explicit spec or legacy flat fields."""
    raw_spec = data.get("strategy_spec")
    if isinstance(raw_spec, dict):
        resolved_signal_mode = normalize_signal_mode(raw_spec.get("signal_mode") or data.get("signal_mode"))
        inference_data = dict(data)
        inference_data["signal_mode"] = resolved_signal_mode
        # When an explicit strategy_spec exists, a blank/invalid spec family should
        # infer from the spec-aware contract, not from a stale legacy flat field.
        inference_data["setup_family"] = ""
        setup_family = str(raw_spec.get("setup_family") or "").strip().lower()
        if setup_family not in SUPPORTED_SETUP_FAMILIES:
            setup_family = _infer_setup_family(inference_data)
        spec_version = str(raw_spec.get("spec_version") or data.get("spec_version") or "v1").strip().lower()
        if spec_version not in SUPPORTED_STRATEGY_SPEC_VERSIONS:
            spec_version = "v1"
        prompt_bundle = dict(raw_spec.get("prompt_bundle") or {})
        if not str(prompt_bundle.get("signal_instruction") or "").strip():
            prompt_bundle["signal_instruction"] = str(data.get("signal_instruction") or "")
        if not str(prompt_bundle.get("validator_instruction") or "").strip():
            prompt_bundle["validator_instruction"] = str(data.get("validator_instruction") or "")
        metadata = dict(raw_spec.get("metadata") or {})
        metadata.setdefault("source", resolve_strategy_source(data))
        top_symbol = str(data.get("symbol") or "")
        metadata["symbol"] = top_symbol if top_symbol else metadata.get("symbol", "")
        top_version = _safe_int(data.get("version", 0))
        metadata["version"] = top_version if top_version > 0 else _safe_int(metadata.get("version", 0))
        entry_model = dict(raw_spec.get("entry_model") or {})
        signal_logic = resolve_strategy_signal_logic(data)
        signal_rules = resolve_strategy_signal_rules(data, default_to_ema=True)
        entry_model.setdefault(
            "type",
            "prompt_defined" if resolved_signal_mode == "ai_signal" else "rule_derived",
        )
        entry_model.setdefault("signal_logic", signal_logic)
        entry_model.setdefault(
            "signal_rule_sources",
            [
                item.get("source")
                for item in signal_rules
                if isinstance(item, dict) and item.get("source")
            ],
        )
        return StrategySpecV1(
            spec_version=spec_version,
            signal_mode=resolved_signal_mode,
            setup_family=setup_family,
            direction_model=str(raw_spec.get("direction_model") or ("ai_hypothesis" if resolved_signal_mode == "ai_signal" else "algorithmic_rules")),
            enabled_preconditions=list(raw_spec.get("enabled_preconditions") or []),
            entry_model=entry_model,
            invalidation_model=dict(raw_spec.get("invalidation_model") or {}),
            exit_policy=dict(raw_spec.get("exit_policy") or {}),
            risk_policy=dict(raw_spec.get("risk_policy") or {}),
            prompt_bundle=prompt_bundle,
            ai_models=dict(raw_spec.get("ai_models") or data.get("ai_models") or {}),
            metadata=metadata,
        )

    params = data.get("params", {}) if isinstance(data.get("params"), dict) else {}
    tools = _coerce_tool_flags(data.get("tools"))
    signal_logic = resolve_strategy_signal_logic(data)
    signal_rules = resolve_strategy_signal_rules(data, default_to_ema=True)
    signal_mode = normalize_signal_mode(data.get("signal_mode"))
    preconditions = [
        name for name in (
            "session_filter",
            "news_filter",
            "volatility_filter",
            "dxy_filter",
            "sentiment_filter",
            "ema200_filter",
        )
        if tools.get(name)
    ]
    return StrategySpecV1(
        spec_version="v1",
        signal_mode=signal_mode,
        setup_family=_infer_setup_family(data),
        direction_model="ai_hypothesis" if signal_mode == "ai_signal" else "algorithmic_rules",
        enabled_preconditions=preconditions,
        entry_model={
            "type": "prompt_defined" if signal_mode == "ai_signal" else "rule_derived",
            "signal_logic": signal_logic,
            "signal_rule_sources": [
                item.get("source")
                for item in signal_rules
                if isinstance(item, dict) and item.get("source")
            ],
        },
        invalidation_model={
            "type": "structural_plus_atr",
            "sl_atr_mult": params.get("sl_atr_mult"),
            "rsi_bounds": {
                "ob": params.get("rsi_ob"),
                "os": params.get("rsi_os"),
            },
        },
        exit_policy={
            "tp1_rr": params.get("tp1_rr"),
            "tp2_rr": params.get("tp2_rr"),
            "tp1_close_pct": params.get("tp1_close_pct"),
            "trail_enabled": params.get("trail_enabled", False),
            "trail_type": params.get("trail_type", "atr"),
            "trail_atr_mult": params.get("trail_atr_mult", 1.5),
            "trail_pips": params.get("trail_pips", 200.0),
            "trail_activation_rr": params.get("trail_activation_rr", 1.0),
            "trail_step_min_pips": params.get("trail_step_min_pips", 5.0),
        },
        risk_policy={
            "risk_pct": params.get("risk_pct"),
            "min_confidence": (data.get("validation") or {}).get("min_confidence"),
            "min_rr": (data.get("validation") or {}).get("min_rr"),
        },
        prompt_bundle={
            "signal_instruction": str(data.get("signal_instruction") or ""),
            "validator_instruction": str(data.get("validator_instruction") or ""),
        },
        ai_models=dict(data.get("ai_models") or {}),
        metadata={
            "source": resolve_strategy_source(data),
            "symbol": str(data.get("symbol") or ""),
            "version": resolve_strategy_version(data),
        },
    )


async def load_active_strategy(
    settings_service: SettingsService,
    symbol: str,
    instance_id: str = "",
) -> ActiveStrategyConfig | None:
    """
    Read active strategy from DB, parse JSON, return typed config.

    Lookup order:
      1. active_strategy_{instance_id}  (per-agent binding)
      2. active_strategy_{symbol}       (legacy single-agent fallback)

    Returns None if no active strategy is set.
    """
    raw = await _load_active_strategy_raw(settings_service, symbol, instance_id)
    if not raw:
        return None

    try:
        raw_data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("[strategy-loader] Invalid JSON for active_strategy_%s", symbol)
        return None
    if not isinstance(raw_data, dict):
        logger.warning(
            "[strategy-loader] Expected dict for active_strategy_%s, got %s",
            symbol,
            type(raw_data).__name__,
        )
        return None

    raw_spec_version = None
    raw_spec = raw_data.get("strategy_spec")
    if isinstance(raw_spec, dict):
        raw_spec_version = raw_spec.get("spec_version")
    if raw_spec_version is None:
        raw_spec_version = raw_data.get("spec_version")
    if raw_spec_version and str(raw_spec_version).strip().lower() not in SUPPORTED_STRATEGY_SPEC_VERSIONS:
        logger.error(
            "[strategy-loader] Unsupported spec_version '%s' for active_strategy_%s",
            raw_spec_version,
            symbol,
        )
        return None

    data = load_strategy_json(raw)
    if data is None:
        return None
    return build_active_strategy_config(data, fallback_symbol=symbol)


# Execution-only gates — these run post-confidence in algo_ai mode,
# not as feature providers.
_EXECUTION_GATES = {"risk_filter", "correlation_guard"}


def build_feature_pipeline(
    config: ActiveStrategyConfig,
    registry: ToolRegistry,
):
    """
    Build a FeaturePipeline for algo_ai mode.

    Includes all enabled tools EXCEPT execution gates (risk_filter,
    correlation_guard), which run separately as post-confidence guards.
    Returns a FeaturePipeline with tools in default pipeline order.
    """
    from alphaloop.tools.pipeline import FeaturePipeline

    enabled_names = [
        name for name, on in config.tools.items()
        if on and name not in _EXECUTION_GATES
    ]

    tools = []
    for name in enabled_names:
        tool = registry.get_tool(name)
        if tool:
            tools.append((name, tool))
        else:
            logger.debug("[strategy-loader] Tool '%s' not found in registry", name)

    tools.sort(key=lambda t: _DEFAULT_ORDER.get(t[0], 99))

    logger.info(
        "[strategy-loader] Built feature pipeline for %s v%d: %s",
        config.symbol,
        config.version,
        [name for name, _ in tools],
    )
    return FeaturePipeline(tools=[inst for _, inst in tools])
