"""
MT5 order execution — async wrapper around the sync MetaTrader5 API.
All MT5 calls are run via asyncio.to_thread to avoid blocking the event loop.
"""

import asyncio
import logging
import threading
import uuid
from datetime import datetime, timezone

from alphaloop.config.assets import get_asset_config
from alphaloop.execution.order_state import OrderRegistry, OrderState
from alphaloop.execution.schemas import OrderResult, Position

logger = logging.getLogger(__name__)


class RehearsalModeError(RuntimeError):
    """Raised when order submission is attempted in rehearsal mode (Phase 5D)."""
    pass


# Thread-safe counter for dry-run ticket IDs
_dry_ticket_counter = 0
_dry_ticket_lock = threading.Lock()


class MT5Executor:
    """
    Wraps MetaTrader5 Python API for async order execution.
    Dry-run mode logs intended orders without sending to broker.
    """

    def __init__(
        self,
        symbol: str = "XAUUSD",
        *,
        magic: int = 123456,
        deviation: int = 20,
        dry_run: bool = True,
        server: str = "",
        login: int = 0,
        password: str = "",
        rehearsal: bool = False,
    ):
        asset = get_asset_config(symbol)
        self.symbol = asset.mt5_symbol
        self.magic = magic
        self.dry_run = dry_run
        self.rehearsal = rehearsal  # Phase 5D: code-enforced no-order mode
        self._mt5 = None
        self._connected = False

        # Asset-aware deviation
        if asset.asset_class == "crypto":
            self.deviation = 50
        elif asset.asset_class in ("spot_metal", "commodity"):
            self.deviation = 20
        else:
            self.deviation = deviation

        self._server = server
        self._login = login
        self._password = password

        # Order lifecycle tracking
        self.order_registry = OrderRegistry()

    async def connect(self) -> bool:
        """Initialize and connect to MT5 terminal (always, even dry-run needs price data)."""
        try:
            import MetaTrader5 as mt5
            self._mt5 = mt5

            ok = await asyncio.to_thread(
                mt5.initialize,
                server=self._server,
                login=self._login,
                password=self._password,
            )
            if not ok:
                err = mt5.last_error()
                logger.error("MT5 init failed: %s", err)
                return False

            info = await asyncio.to_thread(mt5.account_info)
            logger.info(
                "MT5 connected | Account: %s | Balance: $%s | Server: %s",
                info.login, f"{info.balance:,.2f}", info.server,
            )
            self._connected = True
            return True
        except ImportError:
            logger.error("MetaTrader5 package not installed")
            return False
        except Exception as e:
            logger.error("MT5 connection failed: %s", e)
            return False

    async def verify_identity(
        self,
        *,
        expected_account: int = 0,
        expected_server: str = "",
    ) -> tuple[bool, str]:
        """Verify broker account identity. Returns (ok, error_detail).

        Checks:
        - Account login matches expected_account (if nonzero)
        - Server name matches expected_server (if non-empty)
        - Terminal has trade permission (not investor/read-only)
        - Trading symbol is visible and tradeable
        """
        if not self._mt5 or not self._connected:
            return False, "MT5 not connected"

        mt5 = self._mt5
        info = await asyncio.to_thread(mt5.account_info)
        if info is None:
            return False, "Failed to retrieve account info"

        # Check account login
        if expected_account and info.login != expected_account:
            return False, (
                f"Account mismatch: connected to {info.login}, "
                f"expected {expected_account}"
            )

        # Check server
        if expected_server and info.server != expected_server:
            return False, (
                f"Server mismatch: connected to '{info.server}', "
                f"expected '{expected_server}'"
            )

        # Check trade permission (trade_allowed on account level)
        if not info.trade_allowed:
            return False, (
                f"Account {info.login} does not have trade permission "
                f"(investor/read-only mode)"
            )

        # Check terminal-level trade permission
        terminal_info = await asyncio.to_thread(mt5.terminal_info)
        if terminal_info and not terminal_info.trade_allowed:
            return False, "Terminal does not allow trading (check AutoTrading button)"

        # Check symbol visibility and trade mode
        sym_info = await asyncio.to_thread(mt5.symbol_info, self.symbol)
        if sym_info is None:
            return False, f"Symbol '{self.symbol}' not found on server"

        if not sym_info.visible:
            # Try to enable it
            selected = await asyncio.to_thread(mt5.symbol_select, self.symbol, True)
            if not selected:
                return False, f"Symbol '{self.symbol}' not visible and cannot be selected"

        # trade_mode: 0=disabled, 1=long only, 2=short only, 3=close only, 4=full
        if sym_info.trade_mode == 0:
            return False, f"Symbol '{self.symbol}' trading is disabled (trade_mode=0)"
        if sym_info.trade_mode == 3:
            return False, f"Symbol '{self.symbol}' is close-only (trade_mode=3)"

        logger.info(
            "[broker-identity] Verified: account=%d server='%s' symbol='%s' trade_mode=%d",
            info.login, info.server, self.symbol, sym_info.trade_mode,
        )
        return True, ""

    async def open_order(
        self,
        direction: str,
        lots: float,
        sl: float,
        tp: float,
        *,
        tp2: float | None = None,
        comment: str = "",
    ) -> OrderResult:
        """Place a market order."""
        # Phase 5D: Rehearsal mode — block all order submission
        if self.rehearsal:
            raise RehearsalModeError(
                "Order submission blocked: executor is in rehearsal mode. "
                "Read operations (prices, positions) are allowed; order placement is not."
            )

        global _dry_ticket_counter

        order_id = uuid.uuid4().hex[:12]
        tracker = self.order_registry.create(
            order_id=order_id,
            symbol=self.symbol,
            direction=direction,
            lots=lots,
            requested_price=0.0,
        )

        if self.dry_run:
            with _dry_ticket_lock:
                _dry_ticket_counter += 1
                ticket = _dry_ticket_counter
            # Simulate fill at current price
            price = await self._get_price(direction)
            tracker.requested_price = price
            tracker.mark_sent(ticket)
            tracker.mark_filled(price, lots)
            tracker.mark_verified()
            logger.info(
                "[DRY-RUN] %s %s %.2f lots @ %.5f SL=%.5f TP=%.5f",
                direction, self.symbol, lots, price, sl, tp,
            )
            return OrderResult(
                success=True,
                order_ticket=ticket,
                fill_price=price,
                fill_volume=lots,
                spread_at_fill=0.0,
                slippage_points=0.0,
            )

        if not self._mt5:
            return OrderResult(success=False, error_message="MT5 not connected")

        mt5 = self._mt5
        try:
            order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
            tick = await asyncio.to_thread(mt5.symbol_info_tick, self.symbol)
            if tick is None:
                tracker.mark_failed("No tick data")
                return OrderResult(success=False, error_message="No tick data")

            price = tick.ask if direction == "BUY" else tick.bid
            spread = abs(tick.ask - tick.bid)
            tracker.requested_price = price

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": self.symbol,
                "volume": lots,
                "type": order_type,
                "price": price,
                "sl": sl,
                "tp": tp,
                "deviation": self.deviation,
                "magic": self.magic,
                "comment": comment or "alphaloop_v3",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

            tracker.transition(OrderState.SENT, "order_send called")
            result = await asyncio.to_thread(mt5.order_send, request)
            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                err_msg = f"MT5 error: {result.retcode if result else 'null'}"
                if result and hasattr(result, "comment"):
                    err_msg += f" — {result.comment}"
                tracker.mark_rejected(
                    error_code=result.retcode if result else -1,
                    error_message=err_msg,
                )
                return OrderResult(
                    success=False,
                    error_code=result.retcode if result else -1,
                    error_message=err_msg,
                )

            slippage = abs(result.price - price) if result.price else 0.0
            self.order_registry.register_ticket(order_id, result.order)
            tracker.mark_filled(result.price, result.volume, slippage, spread)
            # Record execution metrics
            from alphaloop.monitoring.metrics import metrics_tracker as _mt
            _mt.record_sync("slippage_pips", slippage)
            return OrderResult(
                success=True,
                order_ticket=result.order,
                fill_price=result.price,
                fill_volume=result.volume,
                spread_at_fill=spread,
                slippage_points=slippage,
            )
        except Exception as e:
            logger.error("MT5 order failed: %s", e)
            return OrderResult(success=False, error_message=str(e))

    async def close_position(self, ticket: int, lots: float | None = None) -> OrderResult:
        """Close an open position by ticket."""
        if self.rehearsal:
            raise RehearsalModeError(
                "Position close blocked: executor is in rehearsal mode."
            )
        if self.dry_run:
            logger.info("[DRY-RUN] Close position ticket=%d", ticket)
            return OrderResult(success=True, order_ticket=ticket)

        if not self._mt5:
            return OrderResult(success=False, error_message="MT5 not connected")

        mt5 = self._mt5
        try:
            positions = await asyncio.to_thread(mt5.positions_get, ticket=ticket)
            if not positions:
                return OrderResult(success=False, error_message=f"Position {ticket} not found")

            pos = positions[0]
            close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            tick = await asyncio.to_thread(mt5.symbol_info_tick, pos.symbol)
            if tick is None:
                return OrderResult(success=False, error_message=f"No tick data for {pos.symbol}")
            price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
            vol = lots or pos.volume

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": vol,
                "type": close_type,
                "position": ticket,
                "price": price,
                "deviation": self.deviation,
                "magic": self.magic,
                "comment": "alphaloop_v3_close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

            result = await asyncio.to_thread(mt5.order_send, request)
            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                return OrderResult(
                    success=False,
                    error_code=result.retcode if result else -1,
                    error_message=f"Close failed: {result.retcode if result else 'null'}",
                )

            return OrderResult(
                success=True,
                order_ticket=result.order,
                fill_price=result.price,
                fill_volume=result.volume,
            )
        except Exception as e:
            logger.error("Close position failed: %s", e)
            return OrderResult(success=False, error_message=str(e))

    async def modify_sl_tp(
        self, ticket: int, sl: float, tp: float
    ) -> OrderResult:
        """Modify SL/TP on an open position."""
        if self.dry_run:
            logger.info("[DRY-RUN] Modify ticket=%d SL=%.5f TP=%.5f", ticket, sl, tp)
            return OrderResult(success=True, order_ticket=ticket)

        if not self._mt5:
            return OrderResult(success=False, error_message="MT5 not connected")

        mt5 = self._mt5
        try:
            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "symbol": self.symbol,
                "position": ticket,
                "sl": sl,
                "tp": tp,
            }
            result = await asyncio.to_thread(mt5.order_send, request)
            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                return OrderResult(
                    success=False,
                    error_message=f"Modify failed: {result.retcode if result else 'null'}",
                )
            return OrderResult(success=True, order_ticket=ticket)
        except Exception as e:
            return OrderResult(success=False, error_message=str(e))

    async def get_open_positions(self) -> list[Position]:
        """Get all open positions for this magic number."""
        if self.dry_run:
            return []

        if not self._mt5:
            return []

        mt5 = self._mt5
        try:
            positions = await asyncio.to_thread(mt5.positions_get, symbol=self.symbol)
            if not positions:
                return []
            return [
                Position(
                    ticket=p.ticket,
                    symbol=p.symbol,
                    direction="BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
                    volume=p.volume,
                    entry_price=p.price_open,
                    current_price=p.price_current,
                    stop_loss=p.sl,
                    take_profit=p.tp,
                    profit_usd=p.profit,
                    swap=p.swap,
                    magic=p.magic,
                    opened_at=datetime.fromtimestamp(p.time, tz=timezone.utc),
                )
                for p in positions
                if p.magic == self.magic
            ]
        except Exception as e:
            logger.error("Get positions failed: %s", e)
            return []

    async def get_account_balance(self) -> float:
        """Get current account balance."""
        if self.dry_run:
            return 10000.0
        if not self._mt5:
            return 0.0
        try:
            info = await asyncio.to_thread(self._mt5.account_info)
            return info.balance if info else 0.0
        except Exception:
            return 0.0

    async def _get_price(self, direction: str) -> float:
        """Get current price for a direction."""
        if not self._mt5:
            logger.warning(
                "_get_price returning 0.0 for %s %s — MT5 not connected (dry-run). "
                "Fill price will be inaccurate.",
                direction, self.symbol,
            )
            return 0.0
        try:
            tick = await asyncio.to_thread(self._mt5.symbol_info_tick, self.symbol)
            if tick:
                return tick.ask if direction == "BUY" else tick.bid
        except Exception:
            pass
        return 0.0

    async def get_current_price(self, symbol: str | None = None) -> dict | None:
        """Get current bid/ask/spread for the spread regime guard."""
        sym = symbol or self.symbol
        if not self._mt5:
            return None
        try:
            tick = await asyncio.to_thread(self._mt5.symbol_info_tick, sym)
            if tick:
                spread = tick.ask - tick.bid
                return {"bid": tick.bid, "ask": tick.ask, "spread": spread}
        except Exception:
            pass
        return None

    async def verify_fill(self, ticket: int) -> dict:
        """
        Verify a fill with the broker by checking the position exists
        and matches expected parameters.

        Returns dict with verification status and position details.
        """
        if self.dry_run:
            return {"verified": True, "dry_run": True, "ticket": ticket}

        if not self._mt5:
            return {"verified": False, "error": "MT5 not connected"}

        mt5 = self._mt5
        try:
            positions = await asyncio.to_thread(mt5.positions_get, ticket=ticket)
            if not positions:
                # Position might have already been closed (SL/TP hit)
                # Check order history
                deals = await asyncio.to_thread(
                    mt5.history_deals_get,
                    position=ticket,
                )
                if deals:
                    return {
                        "verified": True,
                        "ticket": ticket,
                        "status": "closed",
                        "deals": len(deals),
                    }
                return {"verified": False, "error": f"Position {ticket} not found"}

            pos = positions[0]
            return {
                "verified": True,
                "ticket": ticket,
                "status": "open",
                "symbol": pos.symbol,
                "direction": "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL",
                "volume": pos.volume,
                "entry_price": pos.price_open,
                "current_price": pos.price_current,
                "stop_loss": pos.sl,
                "take_profit": pos.tp,
                "profit": pos.profit,
            }
        except Exception as e:
            logger.error("Fill verification failed for ticket=%d: %s", ticket, e)
            return {"verified": False, "error": str(e)}

    async def place_limit_order(
        self,
        direction: str,
        lots: float,
        limit_price: float,
        sl: float,
        tp: float,
        *,
        expiry_hours: float = 24.0,
        comment: str = "",
    ) -> "OrderResult":
        """
        Place a pending limit order (BUY_LIMIT / SELL_LIMIT).

        Parameters
        ----------
        direction : str
            "BUY" or "SELL".
        lots : float
            Order volume.
        limit_price : float
            Price at which the order should fill.
        sl : float
            Stop loss price.
        tp : float
            Take profit price.
        expiry_hours : float
            Order expiry in hours from now. Default 24h.
        comment : str
            Optional broker comment.

        Returns
        -------
        OrderResult
            success=True if order was accepted by broker.
        """
        from datetime import timedelta

        order_id = uuid.uuid4().hex[:12]
        tracker = self.order_registry.create(
            order_id=order_id,
            symbol=self.symbol,
            direction=direction,
            lots=lots,
            requested_price=limit_price,
        )

        if self.dry_run:
            with _dry_ticket_lock:
                global _dry_ticket_counter
                _dry_ticket_counter += 1
                ticket = _dry_ticket_counter
            tracker.mark_sent(ticket)
            tracker.mark_verified()
            logger.info(
                "[DRY-RUN] LIMIT %s %s %.2f lots @ %.5f SL=%.5f TP=%.5f exp=%gh",
                direction, self.symbol, lots, limit_price, sl, tp, expiry_hours,
            )
            return OrderResult(
                success=True,
                order_ticket=ticket,
                fill_price=limit_price,
                fill_volume=lots,
                spread_at_fill=0.0,
                slippage_points=0.0,
            )

        if not self._mt5:
            return OrderResult(success=False, error_message="MT5 not connected")

        mt5 = self._mt5
        try:
            order_type = (
                mt5.ORDER_TYPE_BUY_LIMIT
                if direction == "BUY"
                else mt5.ORDER_TYPE_SELL_LIMIT
            )
            expiry_dt = datetime.now(timezone.utc) + timedelta(hours=expiry_hours)

            request = {
                "action": mt5.TRADE_ACTION_PENDING,
                "symbol": self.symbol,
                "volume": lots,
                "type": order_type,
                "price": limit_price,
                "sl": sl,
                "tp": tp,
                "deviation": self.deviation,
                "magic": self.magic,
                "comment": comment or "alphaloop_v3_limit",
                "type_time": mt5.ORDER_TIME_SPECIFIED,
                "expiration": int(expiry_dt.timestamp()),
            }

            tracker.transition(OrderState.SENT, "limit order_send called")
            result = await asyncio.to_thread(mt5.order_send, request)
            if result is None or result.retcode != mt5.TRADE_RETCODE_PLACED:
                err_msg = f"Limit order error: {result.retcode if result else 'null'}"
                if result and hasattr(result, "comment"):
                    err_msg += f" — {result.comment}"
                tracker.mark_rejected(
                    error_code=result.retcode if result else -1,
                    error_message=err_msg,
                )
                return OrderResult(
                    success=False,
                    error_code=result.retcode if result else -1,
                    error_message=err_msg,
                )

            self.order_registry.register_ticket(order_id, result.order)
            tracker.mark_sent(result.order)
            logger.info(
                "Limit order placed | %s %s %.2f lots @ %.5f | ticket=%d",
                direction, self.symbol, lots, limit_price, result.order,
            )
            return OrderResult(
                success=True,
                order_ticket=result.order,
                fill_price=limit_price,
                fill_volume=lots,
                spread_at_fill=0.0,
                slippage_points=0.0,
            )
        except Exception as e:
            logger.error("Limit order failed: %s", e)
            return OrderResult(success=False, error_message=str(e))

    async def disconnect(self) -> None:
        """Shutdown MT5 connection."""
        if self._mt5 and not self.dry_run:
            try:
                await asyncio.to_thread(self._mt5.shutdown)
            except Exception:
                pass
        self._connected = False
