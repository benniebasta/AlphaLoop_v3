"""
data/yf_catalog.py — yfinance asset catalog for backtest symbol selection.

Provides a structured catalog of all yfinance-compatible tickers organized
by asset class. The frontend backtest form uses this as a dropdown source.

yfinance ticker conventions:
  - Forex:   EURUSD=X, GBPUSD=X, JPY=X (inverted), ...
  - Crypto:  BTC-USD, ETH-USD, SOL-USD, ...
  - Futures: GC=F (gold), SI=F (silver), CL=F (crude oil), NQ=F, ES=F, ...
  - Stocks:  AAPL, MSFT, TSLA, ...
  - ETFs:    SPY, QQQ, GLD, ...
  - Indices: ^GSPC, ^DJI, ^IXIC, ^VIX, ...
"""

from __future__ import annotations


# ── Asset catalog ────────────────────────────────────────────────────────────
# Each entry: (yf_ticker, display_name, broker_symbol_hint)
# broker_symbol_hint is what the user sees and what maps to MT5 if needed

CATALOG: dict[str, list[tuple[str, str, str]]] = {
    "Metals": [
        ("GC=F",    "Gold Futures",              "XAUUSD"),
        ("SI=F",    "Silver Futures",            "XAGUSD"),
        ("PL=F",    "Platinum Futures",          "XPTUSD"),
        ("PA=F",    "Palladium Futures",         "XPDUSD"),
        ("HG=F",    "Copper Futures",            "COPPER"),
    ],
    "Crypto": [
        ("BTC-USD", "Bitcoin",                   "BTCUSD"),
        ("ETH-USD", "Ethereum",                  "ETHUSD"),
        ("SOL-USD", "Solana",                    "SOLUSD"),
        ("BNB-USD", "Binance Coin",              "BNBUSD"),
        ("XRP-USD", "Ripple",                    "XRPUSD"),
        ("ADA-USD", "Cardano",                   "ADAUSD"),
        ("DOGE-USD","Dogecoin",                  "DOGEUSD"),
        ("AVAX-USD","Avalanche",                 "AVAXUSD"),
        ("DOT-USD", "Polkadot",                  "DOTUSD"),
        ("MATIC-USD","Polygon",                  "MATICUSD"),
        ("LINK-USD","Chainlink",                 "LINKUSD"),
        ("UNI-USD", "Uniswap",                   "UNIUSD"),
        ("LTC-USD", "Litecoin",                  "LTCUSD"),
        ("ATOM-USD","Cosmos",                    "ATOMUSD"),
        ("NEAR-USD","NEAR Protocol",             "NEARUSD"),
        ("ARB-USD", "Arbitrum",                  "ARBUSD"),
        ("OP-USD",  "Optimism",                  "OPUSD"),
    ],
    "Forex Majors": [
        ("EURUSD=X","EUR/USD",                   "EURUSD"),
        ("GBPUSD=X","GBP/USD",                   "GBPUSD"),
        ("JPY=X",   "USD/JPY",                   "USDJPY"),
        ("AUDUSD=X","AUD/USD",                   "AUDUSD"),
        ("USDCAD=X","USD/CAD",                   "USDCAD"),
        ("USDCHF=X","USD/CHF",                   "USDCHF"),
        ("NZDUSD=X","NZD/USD",                   "NZDUSD"),
    ],
    "Forex Crosses": [
        ("EURGBP=X","EUR/GBP",                   "EURGBP"),
        ("EURJPY=X","EUR/JPY",                   "EURJPY"),
        ("GBPJPY=X","GBP/JPY",                   "GBPJPY"),
        ("EURCHF=X","EUR/CHF",                   "EURCHF"),
        ("AUDCAD=X","AUD/CAD",                   "AUDCAD"),
        ("AUDJPY=X","AUD/JPY",                   "AUDJPY"),
        ("NZDJPY=X","NZD/JPY",                   "NZDJPY"),
        ("CADJPY=X","CAD/JPY",                   "CADJPY"),
        ("CHFJPY=X","CHF/JPY",                   "CHFJPY"),
        ("GBPCHF=X","GBP/CHF",                   "GBPCHF"),
        ("GBPAUD=X","GBP/AUD",                   "GBPAUD"),
        ("EURAUD=X","EUR/AUD",                   "EURAUD"),
        ("EURNZD=X","EUR/NZD",                   "EURNZD"),
        ("GBPNZD=X","GBP/NZD",                   "GBPNZD"),
        ("GBPCAD=X","GBP/CAD",                   "GBPCAD"),
    ],
    "Indices": [
        ("^GSPC",   "S&P 500",                   "US500"),
        ("^DJI",    "Dow Jones 30",              "US30"),
        ("^IXIC",   "NASDAQ Composite",          "NAS100"),
        ("^RUT",    "Russell 2000",              "US2000"),
        ("^FTSE",   "FTSE 100",                  "UK100"),
        ("^GDAXI",  "DAX 40",                    "GER40"),
        ("^FCHI",   "CAC 40",                    "FRA40"),
        ("^N225",   "Nikkei 225",                "JP225"),
        ("^HSI",    "Hang Seng",                 "HK50"),
        ("^STOXX50E","Euro Stoxx 50",            "EU50"),
        ("^AXJO",   "ASX 200",                   "AUS200"),
    ],
    "Index Futures": [
        ("ES=F",    "S&P 500 E-mini Futures",    "US500"),
        ("NQ=F",    "NASDAQ 100 E-mini Futures",  "NAS100"),
        ("YM=F",    "Dow E-mini Futures",         "US30"),
        ("RTY=F",   "Russell 2000 E-mini",        "US2000"),
    ],
    "Energy": [
        ("CL=F",    "Crude Oil WTI",             "USOIL"),
        ("BZ=F",    "Brent Crude Oil",           "UKOIL"),
        ("NG=F",    "Natural Gas",               "NATGAS"),
        ("RB=F",    "Gasoline RBOB",             "GASOLINE"),
        ("HO=F",    "Heating Oil",               "HEATINGOIL"),
    ],
    "Agriculture": [
        ("ZC=F",    "Corn Futures",              "CORN"),
        ("ZS=F",    "Soybean Futures",           "SOYBEAN"),
        ("ZW=F",    "Wheat Futures",             "WHEAT"),
        ("KC=F",    "Coffee Futures",            "COFFEE"),
        ("CC=F",    "Cocoa Futures",             "COCOA"),
        ("SB=F",    "Sugar Futures",             "SUGAR"),
        ("CT=F",    "Cotton Futures",            "COTTON"),
    ],
    "US Mega-Cap Stocks": [
        ("AAPL",    "Apple",                     "AAPL"),
        ("MSFT",    "Microsoft",                 "MSFT"),
        ("GOOGL",   "Alphabet (Google)",         "GOOGL"),
        ("AMZN",    "Amazon",                    "AMZN"),
        ("NVDA",    "NVIDIA",                    "NVDA"),
        ("META",    "Meta (Facebook)",           "META"),
        ("TSLA",    "Tesla",                     "TSLA"),
        ("BRK-B",   "Berkshire Hathaway B",      "BRK.B"),
        ("JPM",     "JPMorgan Chase",            "JPM"),
        ("V",       "Visa",                      "V"),
        ("UNH",     "UnitedHealth",              "UNH"),
        ("MA",      "Mastercard",                "MA"),
        ("JNJ",     "Johnson & Johnson",         "JNJ"),
        ("HD",      "Home Depot",                "HD"),
        ("PG",      "Procter & Gamble",          "PG"),
    ],
    "US Tech Stocks": [
        ("AMD",     "AMD",                       "AMD"),
        ("INTC",    "Intel",                     "INTC"),
        ("CRM",     "Salesforce",                "CRM"),
        ("ADBE",    "Adobe",                     "ADBE"),
        ("NFLX",    "Netflix",                   "NFLX"),
        ("PYPL",    "PayPal",                    "PYPL"),
        ("UBER",    "Uber",                      "UBER"),
        ("SQ",      "Block (Square)",            "SQ"),
        ("SHOP",    "Shopify",                   "SHOP"),
        ("SNAP",    "Snap Inc",                  "SNAP"),
        ("PLTR",    "Palantir",                  "PLTR"),
        ("COIN",    "Coinbase",                  "COIN"),
        ("MSTR",    "MicroStrategy",             "MSTR"),
    ],
    "Popular ETFs": [
        ("SPY",     "SPDR S&P 500 ETF",         "SPY"),
        ("QQQ",     "Invesco NASDAQ 100 ETF",   "QQQ"),
        ("IWM",     "iShares Russell 2000 ETF",  "IWM"),
        ("GLD",     "SPDR Gold Shares",          "GLD"),
        ("SLV",     "iShares Silver Trust",      "SLV"),
        ("USO",     "United States Oil Fund",    "USO"),
        ("TLT",     "iShares 20+ Year Treasury", "TLT"),
        ("EEM",     "iShares Emerging Markets",  "EEM"),
        ("VIX",     "iPath VIX Short-Term",      "VIX"),
        ("ARKK",    "ARK Innovation ETF",        "ARKK"),
        ("XLF",     "Financial Select SPDR",     "XLF"),
        ("XLE",     "Energy Select SPDR",        "XLE"),
        ("XLK",     "Technology Select SPDR",    "XLK"),
    ],
    "Volatility": [
        ("^VIX",    "CBOE Volatility Index",     "VIX"),
    ],
    "Bonds": [
        ("ZB=F",    "30-Year T-Bond Futures",    "USTBOND"),
        ("ZN=F",    "10-Year T-Note Futures",    "USTNOTE10"),
        ("ZF=F",    "5-Year T-Note Futures",     "USTNOTE5"),
    ],
}

# Flat lookup: broker_symbol → yf_ticker
SYMBOL_TO_YF: dict[str, str] = {}
# Flat lookup: broker_symbol → display_name
SYMBOL_TO_NAME: dict[str, str] = {}

for _group, _items in CATALOG.items():
    for _yf, _name, _broker in _items:
        SYMBOL_TO_YF[_broker] = _yf
        SYMBOL_TO_NAME[_broker] = _name


def get_yf_ticker(symbol: str) -> str:
    """Map a broker symbol to a yfinance ticker. Falls back to identity."""
    return SYMBOL_TO_YF.get(symbol, symbol)


def get_catalog_for_api() -> list[dict]:
    """
    Return the full catalog as a flat list for the frontend API.

    Returns list of dicts:
        [{"symbol": "XAUUSD", "name": "Gold Futures", "yf_ticker": "GC=F", "group": "Metals"}, ...]
    """
    result = []
    for group, items in CATALOG.items():
        for yf_ticker, display_name, broker_symbol in items:
            result.append({
                "symbol": broker_symbol,
                "name": display_name,
                "yf_ticker": yf_ticker,
                "group": group,
            })
    return result
