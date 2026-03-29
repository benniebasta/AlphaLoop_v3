"""
Session filter — blocks trading during low-quality sessions and weekends.

Pipeline order: FIRST — cheapest check; eliminates weekends/off-hours
before any API call.
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult


class SessionFilter(BaseTool):
    """
    Trading session quality gate.

    Sessions and quality scores:
      - london_ny_overlap (13:00-16:00 UTC): 1.0
      - ny_session (13:00-21:00 UTC):         0.85
      - london_session (07:00-16:00 UTC):     0.80
      - asia_late (04:00-07:00 UTC):          0.40
      - asia_early (00:00-04:00 UTC):         0.20
      - weekend: BLOCKED
    """

    name = "session_filter"
    description = "Blocks trading during low-quality sessions and weekends"

    async def run(self, context) -> ToolResult:
        session = context.session

        if session.is_weekend or session.name == "weekend":
            return ToolResult(
                passed=False,
                reason="Weekend — markets closed",
                severity="block",
                size_modifier=0.0,
                data={"session": session.name, "score": session.score},
            )

        min_score = 0.70  # configurable via strategy params

        if session.score < min_score:
            return ToolResult(
                passed=False,
                reason=(
                    f"Session '{session.name}' quality too low "
                    f"(score={session.score:.2f} < min={min_score:.2f})"
                ),
                severity="block",
                size_modifier=0.0,
                data={"session": session.name, "score": session.score},
            )

        # Scale size by session quality (score >= 0.70 guaranteed here)
        if session.score >= 0.90:
            size_mod = 1.0
        elif session.score >= 0.75:
            size_mod = 0.90
        else:
            size_mod = 0.80

        return ToolResult(
            passed=True,
            reason=f"Session '{session.name}' active (score={session.score:.2f})",
            size_modifier=size_mod,
            data={"session": session.name, "score": session.score},
        )
