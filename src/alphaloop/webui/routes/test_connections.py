"""POST /api/test/* — Connection test endpoints for Settings page."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.webui.deps import get_db_session

router = APIRouter(prefix="/api/test", tags=["test"])
logger = logging.getLogger(__name__)


@router.get("/models")
async def list_models() -> dict:
    """Return all available AI models grouped by provider for dropdowns."""
    from alphaloop.ai.model_hub import BUILTIN_MODELS
    models = []
    for m in BUILTIN_MODELS:
        models.append({
            "id": m.id,
            "provider": m.provider.value,
            "display_name": m.display_name,
        })
    return {"models": models}


@router.post("/mt5")
async def test_mt5(session: AsyncSession = Depends(get_db_session)) -> dict:
    """Test MetaTrader 5 connection using stored credentials."""
    from alphaloop.db.repositories.settings_repo import SettingsRepository
    repo = SettingsRepository(session)

    server = await repo.get("MT5_SERVER") or ""
    login = await repo.get("MT5_LOGIN") or ""
    password = await repo.get("MT5_PASSWORD") or ""
    terminal_path = await repo.get("MT5_TERMINAL_PATH") or ""

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
        return result
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.post("/telegram")
async def test_telegram(session: AsyncSession = Depends(get_db_session)) -> dict:
    """Send a test message via Telegram."""
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
                return {"success": True, "message": f"Message sent to chat {chat_id}"}
            else:
                return {"success": False, "message": data.get("description", "Telegram API error")}
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.post("/ollama")
async def test_ollama(
    body: dict,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Test Ollama local endpoint connectivity."""
    from alphaloop.db.repositories.settings_repo import SettingsRepository
    repo = SettingsRepository(session)

    base_url = body.get("base_url") or await repo.get("QWEN_LOCAL_BASE") or "http://localhost:11434/v1"

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{base_url.rstrip('/v1').rstrip('/')}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("name", "?") for m in data.get("models", [])]
                return {"success": True, "message": f"Ollama OK — {len(models)} models: {', '.join(models[:5])}"}
            return {"success": False, "message": f"Ollama responded {resp.status_code}"}
    except Exception as e:
        return {"success": False, "message": f"Cannot reach Ollama at {base_url}: {e}"}


@router.post("/ai-key")
async def test_ai_key(
    body: dict,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Test a specific AI provider + model by sending a simple prompt."""
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
            max_tokens=10, temperature=0, timeout=15.0, max_retries=0,
        )
        return {"success": True, "message": f"{provider}/{model}: {response.strip()[:50]}"}
    except Exception as e:
        return {"success": False, "message": f"{provider}/{model}: {e}"}


@router.post("/ai")
async def test_ai(session: AsyncSession = Depends(get_db_session)) -> dict:
    """Test AI model connection by sending a simple prompt."""
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
            max_tokens=10,
            temperature=0,
            timeout=15.0,
            max_retries=0,
        )
        return {
            "success": True,
            "message": f"{provider}/{model} responded: {response.strip()[:50]}",
        }
    except Exception as e:
        return {"success": False, "message": f"{provider}/{model}: {e}"}
