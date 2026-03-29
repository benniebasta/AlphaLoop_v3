"""
MT5 order execution — async wrapper around the sync MetaTrader5 API.
All MT5 calls are run via asyncio.to_thread to avoid blocking the event loop.
"""

import asyncio
import logging
from datetime import datetime, timezone

from alphaloop.config.assets import get_asset_config
from alphaloop.execution.schemas import OrderResult, Position

logger = logging.getLogger(__name__)

# Counter for dry-run ticket IDs
_dry_ticket_counter = 0


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
    ):
        asset = get_asset_config(symbol)
        self.symbol = asset.mt5_symbol
        self.magic = magic
        self.dry_run = dry_run
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

    async def connect(self) -> bool:
        """Initialize and connect to MT5 terminal."""
        if self.dry_run:
            logger.info("MT5 dry-run mode — no connection needed")
            self._connected = True
            return True

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
        global _dry_ticket_counter

        if self.dry_run:
            _dry_ticket_counter += 1
            # Simulate fill at current price
            price = await self._get_price(direction)
            logger.info(
                "[DRY-RUN] %s %s %.2f lots @ %.5f SL=%.5f TP=%.5f",
                direction, self.symbol, lots, price, sl, tp,
            )
            return OrderResult(
                success=True,
                order_ticket=_dry_ticket_counter,
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
                return OrderResult(success=False, error_message="No tick data")

            price = tick.ask if direction == "BUY" else tick.bid
            spread = abs(tick.ask - tick.bid)

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

            result = await asyncio.to_thread(mt5.order_send, request)
            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                err_msg = f"MT5 error: {result.retcode if result else 'null'}"
                if result and hasattr(result, "comment"):
                    err_msg += f" — {result.comment}"
                return OrderResult(
                    success=False,
                    error_code=result.retcode if result else -1,
                    error_message=err_msg,
                )

            slippage = abs(result.price - price) if result.price else 0.0
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

    async def disconnect(self) -> None:
        """Shutdown MT5 connection."""
        if self._mt5 and not self.dry_run:
            try:
                await asyncio.to_thread(self._mt5.shutdown)
            except Exception:
                pass
        self._connected = False
