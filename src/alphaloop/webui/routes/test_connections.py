"""POST /api/test/* — Connection test endpoints for Settings page."""

from __future__ import annotations

import asyncio
import json
import logging
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.db.models.operator_audit import OperatorAuditLog
from alphaloop.webui.deps import get_config, get_db_session

router = APIRouter(prefix="/api/test", tags=["test"])
logger = logging.getLogger(__name__)


def _require_operator_auth(authorization: str) -> None:
    """Require bearer auth for connection-test actions when AUTH_TOKEN is configured."""
    expected = os.environ.get("AUTH_TOKEN", "")
    if not expected:
        return
    scheme, _, provided = authorization.partition(" ")
    if scheme.lower() != "bearer" or provided.strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


async def _record_operator_audit(
    session: AsyncSession,
    *,
    request: Request | None,
    target: str,
    payload: dict,
) -> None:
    session.add(OperatorAuditLog(
        operator="webui",
        action="connection_test",
        target=target,
        old_value=None,
        new_value=json.dumps(payload, sort_keys=True),
        source_ip=request.client.host if request and request.client else "unknown",
    ))
    await session.commit()


async def _load_mt5_settings(session: AsyncSession) -> dict[str, str]:
    """Load the MT5 connection settings from persisted app settings."""
    from alphaloop.db.repositories.settings_repo import SettingsRepository

    repo = SettingsRepository(session)
    return {
        "server": await repo.get("MT5_SERVER") or "",
        "login": await repo.get("MT5_LOGIN") or "",
        "password": await repo.get("MT5_PASSWORD") or "",
        "terminal_path": await repo.get("MT5_TERMINAL_PATH") or "",
    }


def _fallback_mt5_symbols() -> list[dict]:
    """Return a safe fallback symbol list when MT5 is unavailable."""
    from alphaloop.config.assets import ASSETS

    return [
        {
            "symbol": ac.mt5_symbol,
            "display_name": ac.display_name,
            "asset_symbol": symbol,
            "group": ac.asset_class,
            "visible": True,
            "selected": True,
            "source": "fallback",
        }
        for symbol, ac in ASSETS.items()
    ]


def _normalize_mt5_symbols(raw_symbols) -> list[dict]:
    """Convert MetaTrader5 symbol records into frontend-friendly rows."""
    rows: list[dict] = []
    seen: set[str] = set()
    for item in raw_symbols or []:
        symbol = getattr(item, "name", "") or ""
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        rows.append({
            "symbol": symbol,
            "display_name": getattr(item, "description", "") or symbol,
            "path": getattr(item, "path", "") or "",
            "visible": bool(getattr(item, "visible", True)),
            "selected": bool(getattr(item, "select", getattr(item, "visible", True))),
            "source": "mt5",
        })

    rows.sort(key=lambda row: (not row["visible"], row["symbol"]))
    return rows


@router.get("/models")
async def list_models(
    session: AsyncSession = Depends(get_db_session),
    config=Depends(get_config),
) -> dict:
    """Return AI models filtered to only those whose provider API key is configured.

    Checks both DB settings (user-saved keys) and AppConfig (env/.env keys).
    A provider is available if either source has a non-empty key.
    """
    from alphaloop.ai.model_hub import BUILTIN_MODELS
    from alphaloop.core.types import AIProvider
    from alphaloop.db.repositories.settings_repo import SettingsRepository
    from alphaloop.utils.crypto import decrypt_value

    repo = SettingsRepository(session)
    api_cfg = config.api

    _provider_key_names: dict[str, str] = {
        AIProvider.GEMINI:    "GEMINI_API_KEY",
        AIProvider.ANTHROPIC: "ANTHROPIC_API_KEY",
        AIProvider.OPENAI:    "OPENAI_API_KEY",
        AIProvider.DEEPSEEK:  "DEEPSEEK_API_KEY",
        AIProvider.XAI:       "XAI_API_KEY",
        AIProvider.QWEN:      "QWEN_API_KEY",
    }

    # Env-level keys from AppConfig
    _env_keys: dict[str, str] = {
        AIProvider.GEMINI:    api_cfg.gemini_api_key.get_secret_value(),
        AIProvider.ANTHROPIC: api_cfg.claude_api_key.get_secret_value(),
        AIProvider.OPENAI:    api_cfg.openai_api_key.get_secret_value(),
        AIProvider.DEEPSEEK:  api_cfg.deepseek_api_key.get_secret_value(),
        AIProvider.XAI:       api_cfg.xai_api_key.get_secret_value(),
        AIProvider.QWEN:      api_cfg.qwen_api_key.get_secret_value(),
    }

    configured: dict[str, bool] = {}
    for provider, key_name in _provider_key_names.items():
        # Check DB first (user-saved key overrides env)
        raw = await repo.get(key_name) or ""
        db_key = ""
        if raw:
            try:
                db_key = decrypt_value(raw)
            except Exception:
                db_key = raw
        env_key = _env_keys.get(provider, "")
        configured[provider] = bool((db_key or env_key).strip())

    # Ollama: available if the local endpoint responds (no API key needed)
    ollama_base = (
        await repo.get("QWEN_LOCAL_BASE")
        or api_cfg.qwen_local_base
        or "http://localhost:11434"
    ).strip()
    try:
        import httpx
        async with httpx.AsyncClient(timeout=2.0) as client:
            import re
            ollama_root = re.sub(r'/v1/?$', '', ollama_base.rstrip('/'))
            resp = await client.get(f"{ollama_root}/api/tags")
            configured[AIProvider.OLLAMA] = resp.status_code == 200
    except Exception:
        configured[AIProvider.OLLAMA] = False

    models = []
    for m in BUILTIN_MODELS:
        if not configured.get(m.provider, False):
            continue
        models.append({
            "id": m.id,
            "provider": m.provider.value,
            "display_name": m.display_name,
            "roles": m.roles,
            "cost_tier": m.cost_tier,
        })
    return {"models": models}


@router.post("/mt5")
async def test_mt5(
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Test MetaTrader 5 connection using stored credentials."""
    _require_operator_auth(authorization)
    settings = await _load_mt5_settings(session)
    server = settings["server"]
    login = settings["login"]
    password = settings["password"]
    terminal_path = settings["terminal_path"]

    if not server or not login:
        return {"success": False, "message": "MT5 server and login not configured"}

    def _test():
        try:
            import MetaTrader5 as mt5
        except ImportError:
            return {"success": False, "message": "MetaTrader5 package not installed"}

        kwargs = {"server": server}
        if login:
            kwargs["login"] = int(login)
        if password:
            # Decrypt if encrypted
            from alphaloop.utils.crypto import decrypt_value
            kwargs["password"] = decrypt_value(password)
        if terminal_path:
            kwargs["path"] = terminal_path

        if not mt5.initialize(**kwargs):
            err = mt5.last_error()
            return {"success": False, "message": f"MT5 init failed: {err}"}

        info = mt5.account_info()
        if info is None:
            mt5.shutdown()
            return {"success": False, "message": "Connected but cannot read account info"}

        result = {
            "success": True,
            "message": f"Connected to {info.server} — {info.name} | Balance: ${info.balance:.2f} | Leverage: 1:{info.leverage}",
        }
        mt5.shutdown()
        return result

    try:
        result = await asyncio.to_thread(_test)
        await _record_operator_audit(
            session,
            request=request,
            target="mt5",
            payload=result,
        )
        return result
    except Exception as e:
        result = {"success": False, "message": str(e)}
        await _record_operator_audit(
            session,
            request=request,
            target="mt5",
            payload=result,
        )
        return result


@router.get("/mt5/symbols")
async def list_mt5_symbols(session: AsyncSession = Depends(get_db_session)) -> dict:
    """Return the symbol list from MT5, with a fallback to configured assets."""
    settings = await _load_mt5_settings(session)
    server = settings["server"]
    login = settings["login"]
    password = settings["password"]
    terminal_path = settings["terminal_path"]

    fallback = _fallback_mt5_symbols()
    if not server or not login:
        return {
            "success": False,
            "message": "MT5 server and login not configured",
            "source": "fallback",
            "symbols": fallback,
        }

    def _load():
        try:
            import MetaTrader5 as mt5
        except ImportError:
            return {
                "success": False,
                "message": "MetaTrader5 package not installed",
                "source": "fallback",
                "symbols": fallback,
            }

        kwargs = {"server": server}
        try:
            kwargs["login"] = int(login)
        except (TypeError, ValueError):
            return {
                "success": False,
                "message": "MT5 login is not a valid number",
                "source": "fallback",
                "symbols": fallback,
            }
        if password:
            from alphaloop.utils.crypto import decrypt_value
            kwargs["password"] = decrypt_value(password)
        if terminal_path:
            kwargs["path"] = terminal_path

        if not mt5.initialize(**kwargs):
            err = mt5.last_error()
            return {
                "success": False,
                "message": f"MT5 init failed: {err}",
                "source": "fallback",
                "symbols": fallback,
            }

        try:
            rows = _normalize_mt5_symbols(mt5.symbols_get() or [])
            if not rows:
                return {
                    "success": False,
                    "message": "MT5 returned no symbols",
                    "source": "fallback",
                    "symbols": fallback,
                }
            return {
                "success": True,
                "message": f"Loaded {len(rows)} MT5 symbols",
                "source": "mt5",
                "symbols": rows,
            }
        finally:
            try:
                mt5.shutdown()
            except Exception:
                pass

    try:
        result = await asyncio.to_thread(_load)
    except Exception as e:
        result = {
            "success": False,
            "message": str(e),
            "source": "fallback",
            "symbols": fallback,
        }

    if result.get("source") == "fallback" and not result.get("symbols"):
        result["symbols"] = fallback
    return result


@router.post("/telegram")
async def test_telegram(
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Send a test message via Telegram."""
    _require_operator_auth(authorization)
    from alphaloop.db.repositories.settings_repo import SettingsRepository
    repo = SettingsRepository(session)

    token = await repo.get("TELEGRAM_TOKEN") or ""
    chat_id = await repo.get("TELEGRAM_CHAT_ID") or ""

    if not token or not chat_id:
        return {"success": False, "message": "Telegram token and chat ID not configured"}

    # Decrypt token if encrypted
    try:
        from alphaloop.utils.crypto import decrypt_value
        token = decrypt_value(token)
    except Exception:
        pass

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": "🤖 AlphaLoop v3 — Test message successful!",
                    "parse_mode": "HTML",
                },
            )
            data = resp.json()
            if data.get("ok"):
                result = {"success": True, "message": f"Message sent to chat {chat_id}"}
            else:
                result = {"success": False, "message": data.get("description", "Telegram API error")}
            await _record_operator_audit(
                session,
                request=request,
                target="telegram",
                payload=result,
            )
            return result
    except Exception as e:
        result = {"success": False, "message": str(e)}
        await _record_operator_audit(
            session,
            request=request,
            target="telegram",
            payload=result,
        )
        return result


@router.post("/ollama")
async def test_ollama(
    body: dict,
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Test Ollama local endpoint connectivity."""
    _require_operator_auth(authorization)
    from alphaloop.db.repositories.settings_repo import SettingsRepository
    repo = SettingsRepository(session)

    base_url = (body.get("base_url") or await repo.get("QWEN_LOCAL_BASE") or "http://localhost:11434/v1").strip()

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            import re
            ollama_root = re.sub(r'/v1/?$', '', base_url.rstrip('/'))
            resp = await client.get(f"{ollama_root}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("name", "?") for m in data.get("models", [])]
                result = {"success": True, "message": f"Ollama OK - {len(models)} models: {', '.join(models[:5])}"}
                await _record_operator_audit(
                    session,
                    request=request,
                    target="ollama",
                    payload=result,
                )
                return result
                return {"success": True, "message": f"Ollama OK — {len(models)} models: {', '.join(models[:5])}"}
            result = {"success": False, "message": f"Ollama responded {resp.status_code}"}
            await _record_operator_audit(
                session,
                request=request,
                target="ollama",
                payload=result,
            )
            return result
    except Exception as e:
        result = {"success": False, "message": f"Cannot reach Ollama at {base_url}: {e}"}
        await _record_operator_audit(
            session,
            request=request,
            target="ollama",
            payload=result,
        )
        return result


@router.post("/ai-key")
async def test_ai_key(
    body: dict,
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Test a specific AI provider + model by sending a simple prompt."""
    _require_operator_auth(authorization)
    from alphaloop.db.repositories.settings_repo import SettingsRepository
    repo = SettingsRepository(session)

    provider = body.get("provider", "gemini")
    model = body.get("model", "gemini-2.5-flash")

    key_map = {
        "gemini": "GEMINI_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "xai": "XAI_API_KEY",
        "qwen": "QWEN_API_KEY",
    }
    key_name = key_map.get(provider)
    api_key = ""
    if key_name:
        raw = await repo.get(key_name) or ""
        if raw:
            try:
                from alphaloop.utils.crypto import decrypt_value
                api_key = decrypt_value(raw)
            except Exception:
                api_key = raw

    if not api_key and provider != "ollama":
        return {"success": False, "message": f"No API key set for {provider}"}

    try:
        from alphaloop.ai.caller import AICaller
        caller = AICaller(api_keys={provider: api_key})
        response = await caller.call_model(
            model,
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
            max_tokens=100, temperature=0, timeout=15.0, max_retries=0,
            response_mime_type="text/plain", thinking_budget=0,
        )
        result = {"success": True, "message": f"{provider}/{model}: {response.strip()[:50]}"}
        await _record_operator_audit(
            session,
            request=request,
            target=f"ai-key:{provider}",
            payload=result,
        )
        return result
    except Exception as e:
        result = {"success": False, "message": f"{provider}/{model}: {e}"}
        await _record_operator_audit(
            session,
            request=request,
            target=f"ai-key:{provider}",
            payload=result,
        )
        return result


@router.post("/news")
async def test_news(
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Test the configured news provider, falling back to ForexFactory."""
    _require_operator_auth(authorization)
    from alphaloop.db.repositories.settings_repo import SettingsRepository
    from alphaloop.utils.crypto import decrypt_value
    import httpx

    repo = SettingsRepository(session)
    provider = (await repo.get("NEWS_PROVIDER") or "forexfactory").lower()
    audit_target = f"news:{provider}"

    async def _audit(result: dict) -> dict:
        await _record_operator_audit(
            session,
            request=request,
            target=audit_target,
            payload=result,
        )
        return result

    def _get_key(raw: str) -> str:
        if not raw:
            return ""
        try:
            return decrypt_value(raw)
        except Exception:
            return raw

    finnhub_key = _get_key(await repo.get("FINNHUB_API_KEY") or "")
    fmp_key = _get_key(await repo.get("FMP_API_KEY") or "")

    # Test primary provider
    if provider == "finnhub":
        if not finnhub_key:
            return await _audit({"success": False, "message": "Finnhub selected but no API key set"})
        try:
            from datetime import datetime, timedelta, timezone
            now = datetime.now(timezone.utc)
            url = (f"https://finnhub.io/api/v1/calendar/economic"
                   f"?from={now.strftime('%Y-%m-%d')}&to={(now + timedelta(days=7)).strftime('%Y-%m-%d')}"
                   f"&token={finnhub_key}")
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
            if isinstance(data, dict) and "economicCalendar" in data:
                events = data["economicCalendar"]
                high = sum(1 for e in events if (e.get("impact") or "").lower() == "high")
                return await _audit({"success": True, "message": f"Finnhub OK - {len(events)} events next 7 days ({high} HIGH impact)"})
                return {"success": True, "message": f"Finnhub OK — {len(events)} events next 7 days ({high} HIGH impact)"}
            err = data.get("error", str(data)[:80]) if isinstance(data, dict) else str(data)[:80]
            return await _audit({"success": False, "message": f"Finnhub: {err} - falling back to ForexFactory"})
            return {"success": False, "message": f"Finnhub: {err} — falling back to ForexFactory"}
        except Exception as e:
            return await _audit({"success": False, "message": f"Finnhub failed: {e} - falling back to ForexFactory"})
            return {"success": False, "message": f"Finnhub failed: {e} — falling back to ForexFactory"}

    elif provider == "fmp":
        if not fmp_key:
            return await _audit({"success": False, "message": "FMP selected but no API key set"})
        try:
            from datetime import datetime, timedelta, timezone
            now = datetime.now(timezone.utc)
            url = (f"https://financialmodelingprep.com/api/v3/economic_calendar"
                   f"?from={now.strftime('%Y-%m-%d')}&to={(now + timedelta(days=7)).strftime('%Y-%m-%d')}"
                   f"&apikey={fmp_key}")
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
            if isinstance(data, list):
                high = sum(1 for e in data if (e.get("impact") or "").upper() == "HIGH")
                return await _audit({"success": True, "message": f"FMP OK - {len(data)} events next 7 days ({high} HIGH impact)"})
                return {"success": True, "message": f"FMP OK — {len(data)} events next 7 days ({high} HIGH impact)"}
            err = data.get("Error Message") or data.get("message") or str(data)[:80]
            return await _audit({"success": False, "message": f"FMP: {err} - falling back to ForexFactory"})
            return {"success": False, "message": f"FMP: {err} — falling back to ForexFactory"}
        except Exception as e:
            return await _audit({"success": False, "message": f"FMP failed: {e} - falling back to ForexFactory"})
            return {"success": False, "message": f"FMP failed: {e} — falling back to ForexFactory"}

    # ForexFactory (default)
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json")
            resp.raise_for_status()
            events = resp.json()
        if not isinstance(events, list):
            return await _audit({"success": False, "message": f"Unexpected FF response: {str(events)[:80]}"})
        high = sum(1 for e in events if (e.get("impact") or "").lower() == "high")
        return await _audit({"success": True, "message": f"ForexFactory OK - {len(events)} events this week ({high} HIGH impact)"})
        return {"success": True, "message": f"ForexFactory OK — {len(events)} events this week ({high} HIGH impact)"}
    except Exception as e:
        return await _audit({"success": False, "message": f"ForexFactory feed failed: {e}"})


@router.post("/ai")
async def test_ai(
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Test AI model connection by sending a simple prompt."""
    _require_operator_auth(authorization)
    from alphaloop.db.repositories.settings_repo import SettingsRepository
    repo = SettingsRepository(session)

    model = await repo.get("SIGNAL_MODEL") or "gemini-2.5-flash"
    provider = await repo.get("SIGNAL_PROVIDER") or "gemini"

    # Resolve API key for provider
    key_map = {
        "gemini": "GEMINI_API_KEY",
        "openai": "OPENAI_API_KEY",
        "claude": "ANTHROPIC_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "xai": "XAI_API_KEY",
        "qwen": "QWEN_API_KEY",
    }
    key_name = key_map.get(provider)
    api_key = ""
    if key_name:
        raw = await repo.get(key_name) or ""
        if raw:
            try:
                from alphaloop.utils.crypto import decrypt_value
                api_key = decrypt_value(raw)
            except Exception:
                api_key = raw

    if not api_key and provider != "ollama":
        return {"success": False, "message": f"No API key configured for {provider}"}

    try:
        from alphaloop.ai.caller import AICaller
        caller = AICaller(api_keys={provider: api_key})
        response = await caller.call_model(
            model,
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
            max_tokens=100,
            temperature=0,
            timeout=15.0,
            max_retries=0,
            response_mime_type="text/plain",
            thinking_budget=0,
        )
        result = {
            "success": True,
            "message": f"{provider}/{model} responded: {response.strip()[:50]}",
        }
        await _record_operator_audit(
            session,
            request=request,
            target=f"ai:{provider}",
            payload=result,
        )
        return result
    except Exception as e:
        result = {"success": False, "message": f"{provider}/{model}: {e}"}
        await _record_operator_audit(
            session,
            request=request,
            target=f"ai:{provider}",
            payload=result,
        )
        return result
