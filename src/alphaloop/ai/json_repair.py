"""
ai/json_repair.py — Best-effort repair of truncated or malformed JSON from LLM responses.

Extracted from signals/engine.py to be reusable across providers and validators.
"""

from __future__ import annotations


def repair_json(s: str) -> str:
    """
    Close unclosed arrays/objects in truncated JSON.

    Handles the most common LLM truncation pattern: output cut off mid-string
    or mid-value.  Returns a string that is more likely to parse with
    json.loads() — callers should still wrap the result in a try/except.
    """
    depth_obj = depth_arr = 0
    in_str = escape = False
    last_safe = 0

    for i, ch in enumerate(s):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth_obj += 1
            last_safe = i
        elif ch == "}":
            depth_obj -= 1
            last_safe = i + 1
        elif ch == "[":
            depth_arr += 1
            last_safe = i
        elif ch == "]":
            depth_arr -= 1
            last_safe = i + 1
        elif ch == ",":
            last_safe = i

    def _recalc(text: str) -> tuple[int, int]:
        d_obj = d_arr = 0
        in_s = esc = False
        for ch in text:
            if esc:
                esc = False
                continue
            if ch == "\\" and in_s:
                esc = True
                continue
            if ch == '"':
                in_s = not in_s
                continue
            if in_s:
                continue
            if ch == "{":
                d_obj += 1
            elif ch == "}":
                d_obj -= 1
            elif ch == "[":
                d_arr += 1
            elif ch == "]":
                d_arr -= 1
        return d_obj, d_arr

    truncate = in_str or s.rstrip().endswith(":")
    if truncate:
        s = s[:last_safe].rstrip().rstrip(",")
        depth_obj, depth_arr = _recalc(s)
    else:
        s = s.rstrip().rstrip(",")

    s += "]" * max(0, depth_arr) + "}" * max(0, depth_obj)
    return s
