# AlphaLoop v3 — AI Integration

## Purpose
How AI models integrate with the system: provider routing, role system, rate limiting, and prompt design.

---

## Provider Architecture

```
AICaller (ai/caller.py)
  │
  ├── call_model(model_id, messages) → routes by provider
  ├── call_role(role, messages)       → resolves role → model_id → call_model
  │
  └── _dispatch(provider, model_id, messages, api_key)
        │
        ├── "anthropic"   → AnthropicProvider   (ai/providers/anthropic.py)
        ├── "gemini"      → GeminiProvider       (ai/providers/gemini.py)
        ├── "openai"      → OpenAICompatProvider (ai/providers/openai_compat.py)
        ├── "deepseek"    → OpenAICompatProvider
        ├── "xai"         → OpenAICompatProvider
        ├── "qwen"        → OpenAICompatProvider
        └── "ollama"      → OllamaProvider       (ai/providers/ollama.py)
```

---

## Model Hub

**File:** `src/alphaloop/ai/model_hub.py` (~361 lines)

25+ built-in models across 7 providers:

| Provider | Models |
|----------|--------|
| Gemini | gemini-2.5-flash, gemini-2.5-pro, gemini-2.0-flash |
| Anthropic | claude-sonnet-4-6, claude-haiku-4-5, claude-opus-4-6 |
| OpenAI | gpt-4o, gpt-4o-mini, o1, o3-mini |
| DeepSeek | deepseek-chat, deepseek-reasoner |
| xAI | grok-2, grok-3, grok-3-mini |
| Qwen | qwen2.5-7b, qwen2.5-32b, qwen2.5-72b (cloud + local variants) |
| Ollama | qwen2.5:7b, qwen2.5:32b (local) |

---

## Role System

4 AI roles, each independently configurable:

| Role | Purpose | Default Model |
|------|---------|---------------|
| `signal` | Generate trading signals | `gemini-2.5-flash` |
| `validator` | Critique and validate signals | `claude-sonnet-4-6` |
| `research` | Analyze trading performance | `gemini-2.5-flash` |
| `autolearn` | Auto-improve strategy params | `gemini-2.5-flash` |

**Resolution:** `resolve_role(role, settings) → model_id`
1. Check per-strategy override (from strategy version JSON)
2. Check global default (from AI Hub settings)
3. Fall back to hardcoded default

**Configuration:**
- Global: AI Hub page → Section C: Default Role Assignments
- Per-strategy: Strategy card → AI Models panel → 4 dropdowns

---

## Rate Limiting

**File:** `src/alphaloop/ai/rate_limiter.py` (~84 lines)

- `AsyncRateLimiter`: sliding window per provider
- Default: 10 calls per 60 seconds per provider
- Blocks with `asyncio.sleep()` if rate exceeded
- Shared across all callers in the process

---

## API Key Resolution

**Priority:**
1. Injected via `AICaller(api_keys={"anthropic": "sk-..."})`
2. Environment variables: `GEMINI_API_KEY`, `CLAUDE_API_KEY`, `OPENAI_API_KEY`, etc.
3. DB settings (set via WebUI Settings > API Keys)

**Provider → Env Var mapping** (from `PROVIDER_KEY_ENV` in model_hub.py):
| Provider | Env Var |
|----------|---------|
| gemini | `GEMINI_API_KEY` |
| anthropic | `CLAUDE_API_KEY` |
| openai | `OPENAI_API_KEY` |
| deepseek | `DEEPSEEK_API_KEY` |
| xai | `XAI_API_KEY` |
| qwen | `QWEN_API_KEY` |

---

## Prompt Templates

### Signal Generation
**File:** `src/alphaloop/signals/engine.py`

Builds prompt from:
- MarketContext (price, indicators, trend, session, news)
- StrategyParams (timeframe, setup preferences)
- System instructions for JSON output format

Expected output: JSON with `direction`, `setup_type`, `entry_zone`, `sl`, `tp1`, `tp2`, `confidence`, `reasoning`

### Signal Validation
**File:** `src/alphaloop/validation/prompts.py`

Provides the AI validator with:
- The generated signal
- Market context snapshot
- Strategy constraints
- Instructions to critique the signal

### Research Analysis
**File:** `src/alphaloop/research/prompts.py`

Provides the AI with:
- Recent trade history (wins, losses, metrics)
- Current strategy parameters
- Performance trends
- Instructions to suggest parameter improvements

---

## Prompt Injection Detection

**File:** `src/alphaloop/signals/schema.py`

`TradeSignal` Pydantic model includes validation that:
- Checks for suspicious patterns in AI output
- Validates JSON structure matches expected schema
- Rejects signals with anomalous field values

---

## Retry & Fallback

**File:** `src/alphaloop/ai/caller.py`

```python
call_model(
    model_id="gemini-2.5-flash",
    messages=[...],
    max_retries=2,           # Retry on transient errors
    retry_delay=1.0,         # Seconds between retries
    fallback_models=["gpt-4o-mini"],  # Try these if primary fails
)
```

1. Try primary model (up to `max_retries`)
2. On persistent failure: try each fallback model
3. If all fail: raise `AlphaLoopError`

---

## Connection Testing

**Endpoints** (from `webui/routes/test_connections.py`):

| Endpoint | Tests |
|----------|-------|
| `POST /api/test/ai` | Ping configured signal model with test prompt |
| `POST /api/test/ai-key` | Test specific provider API key (body: `{provider, model}`) |
| `POST /api/test/ollama` | Check local Ollama endpoint, list available models |
| `GET /api/test/models` | Return list of all 25+ built-in models for dropdown population |
