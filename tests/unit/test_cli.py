"""Tests for the CLI entry point (alphaloop.main)."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest


def test_main_exists():
    """main() function exists and is callable."""
    from alphaloop.main import main

    assert callable(main)


def test_parse_args_defaults():
    """parse_args() returns correct defaults when no flags are given."""
    from alphaloop.main import parse_args

    with patch("sys.argv", ["alphaloop"]):
        args = parse_args()
        assert args.symbol == "XAUUSD"
        assert args.dry_run is True
        assert args.port == 8090
        assert args.live is False
        assert args.web_only is False
        assert args.instance_id == ""
        assert args.poll_interval == 300.0


def test_parse_args_live_flag():
    """--live flag sets live=True."""
    from alphaloop.main import parse_args

    with patch("sys.argv", ["alphaloop", "--live"]):
        args = parse_args()
        assert args.live is True


def test_parse_args_web_only_flag():
    """--web-only flag sets web_only=True."""
    from alphaloop.main import parse_args

    with patch("sys.argv", ["alphaloop", "--web-only"]):
        args = parse_args()
        assert args.web_only is True


def test_parse_args_symbol_override():
    """--symbol BTCUSD overrides the default symbol."""
    from alphaloop.main import parse_args

    with patch("sys.argv", ["alphaloop", "--symbol", "BTCUSD"]):
        args = parse_args()
        assert args.symbol == "BTCUSD"


def test_parse_args_port_override():
    """--port 9999 overrides the default port."""
    from alphaloop.main import parse_args

    with patch("sys.argv", ["alphaloop", "--port", "9999"]):
        args = parse_args()
        assert args.port == 9999


def test_parse_args_combined_flags():
    """Multiple flags can be combined."""
    from alphaloop.main import parse_args

    with patch(
        "sys.argv",
        ["alphaloop", "--symbol", "EURUSD", "--live", "--web-only", "--port", "7777"],
    ):
        args = parse_args()
        assert args.symbol == "EURUSD"
        assert args.live is True
        assert args.web_only is True
        assert args.port == 7777
