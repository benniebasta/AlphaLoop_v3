import pytest

from alphaloop.webui.routes.strategies import _candidate_gate_bypass


class _FakeRepo:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    async def get(self, key: str, default: str = "") -> str:
        return self._values.get(key, default)


@pytest.mark.asyncio
async def test_candidate_gate_defaults_apply_to_algo_modes():
    repo = _FakeRepo({})

    assert await _candidate_gate_bypass(repo, None, "algo_only") is False
    assert await _candidate_gate_bypass(repo, None, "algo_ai") is False
    assert await _candidate_gate_bypass(repo, "ui_ai_signal_card", "ai_signal") is True


@pytest.mark.asyncio
async def test_candidate_gate_can_be_disabled_for_algo_ai():
    repo = _FakeRepo({"PROMOTION_CANDIDATE_GATE_ALGO_AI": "false"})

    assert await _candidate_gate_bypass(repo, None, "algo_ai") is True
