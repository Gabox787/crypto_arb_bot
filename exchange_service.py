"""
exchange_service.py — прямые HTTP запросы ко всем биржам
CEX: Binance, Bybit, OKX, MEXC, KuCoin, BingX, Bitget, Gate
DEX: DexScreener, CoinGecko (заменил Birdeye — не требует ключа)
"""
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass
from typing import Optional
import aiohttp
 
logger = logging.getLogger(__name__)
FETCH_TIMEOUT = 15
HTTP_TIMEOUT  = aiohttp.ClientTimeout(total=FETCH_TIMEOUT)
DEX_MIN_LIQ   = 100_000   # минимальная ликвидность USD для DEX пар
 
# Токены, для которых DEX Solana нерелевантен (есть только wrapped с другой ценой)
CEX_ONLY_TOKENS = {"BTC", "ETH", "BNB", "XRP", "ADA", "DOGE", "DOT", "LTC", "BCH"}
 
 
def chart_url(exchange: str, ticker: str) -> str:
    t = ticker.upper()
    urls = {
        "Binance":     f"https://www.binance.com/en/trade/{t}_USDT",
        "Bybit":       f"https://www.bybit.com/trade/usdt/{t}USDT",
        "OKX":         f"https://www.okx.com/trade-spot/{t.lower()}-usdt",
        "MEXC":        f"https://www.mexc.com/exchange/{t}_USDT",
        "KuCoin":      f"https://www.kucoin.com/trade/{t}-USDT",
        "BingX":       f"https://bingx.com/en-us/spot/{t}USDT/",
        "Bitget":      f"https://www.bitget.com/spot/{t}USDT",
        "Gate":        f"https://www.gate.io/trade/{t}_USDT",
        "DexScreener": f"https://dexscreener.com/solana/{t}",
        "CoinGecko":   f"https://www.coingecko.com/en/coins/{t.lower()}",
    }
    return urls.get(exchange, "")
 
 
@dataclass
class PriceResult:
    exchange: str
    price: Optional[float] = None
    fair_price: Optional[float] = None
    exchange_type: str = "CEX"
    error: Optional[str] = None
    url: str = ""
 
 
def _make_session() -> aiohttp.ClientSession:
    connector = aiohttp.TCPConnector(
        resolver=aiohttp.AsyncResolver(),
        ssl=True,
        limit=30,
        ttl_dns_cache=300,
    )
    return aiohttp.ClientSession(
        connector=connector,
        timeout=HTTP_TIMEOUT,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Accept": "application/json",
        },
    )
 
 
async def _get(session: aiohttp.ClientSession, url: str, params: dict = None) -> dict | list:
    async with session.get(url, params=params) as r:
        r.raise_for_status()
        return await r.json(content_type=None)
 
 
# ── CEX ────────────────────────────────────────────────────────────────────────
async def _binance(s: aiohttp.ClientSession, t: str) -> PriceResult:
    url = chart_url("Binance", t)
    try:
        d = await _get(s, "https://api.binance.com/api/v3/ticker/bookTicker", {"symbol": f"{t}USDT"})
        bid, ask = float(d["bidPrice"]), float(d["askPrice"])
        price = (bid + ask) / 2
        return PriceResult("Binance", price=price, fair_price=price, url=url)
    except Exception as e:
        return PriceResult("Binance", error=str(e)[:100], url=url)
 
 
async def _bybit(s: aiohttp.ClientSession, t: str) -> PriceResult:
    url = chart_url("Bybit", t)
    try:
        d = await _get(s, "https://api.bybit.com/v5/market/tickers", {"category": "spot", "symbol": f"{t}USDT"})
        item = d["result"]["list"][0]
        bid  = float(item["bid1Price"])
        ask  = float(item["ask1Price"])
        last = float(item["lastPrice"])
        return PriceResult("Bybit", price=last, fair_price=(bid+ask)/2, url=url)
    except Exception as e:
        return PriceResult("Bybit", error=str(e)[:100], url=url)
 
 
async def _okx(s: aiohttp.ClientSession, t: str) -> PriceResult:
    url = chart_url("OKX", t)
    try:
        d = await _get(s, "https://www.okx.com/api/v5/market/ticker", {"instId": f"{t}-USDT"})
        item = d["data"][0]
        bid  = float(item["bidPx"])
        ask  = float(item["askPx"])
        last = float(item["last"])
        return PriceResult("OKX", price=last, fair_price=(bid+ask)/2, url=url)
    except Exception as e:
        return PriceResult("OKX", error=str(e)[:100], url=url)
 
 
async def _mexc(s: aiohttp.ClientSession, t: str) -> PriceResult:
    url = chart_url("MEXC", t)
    try:
        d = await _get(s, "https://api.mexc.com/api/v3/ticker/bookTicker", {"symbol": f"{t}USDT"})
        bid, ask = float(d["bidPrice"]), float(d["askPrice"])
        price = (bid + ask) / 2
        return PriceResult("MEXC", price=price, fair_price=price, url=url)
    except Exception as e:
        return PriceResult("MEXC", error=str(e)[:100], url=url)
 
 
async def _kucoin(s: aiohttp.ClientSession, t: str) -> PriceResult:
    url = chart_url("KuCoin", t)
    try:
        d = await _get(s, "https://api.kucoin.com/api/v1/market/orderbook/level1", {"symbol": f"{t}-USDT"})
        item  = d["data"]
        price = float(item["price"])
        bid   = float(item.get("bestBid") or price)
        ask   = float(item.get("bestAsk") or price)
        return PriceResult("KuCoin", price=price, fair_price=(bid+ask)/2, url=url)
    except Exception as e:
        return PriceResult("KuCoin", error=str(e)[:100], url=url)
 
 
async def _bingx(s: aiohttp.ClientSession, t: str) -> PriceResult:
    url = chart_url("BingX", t)
    try:
        # BingX возвращает список, берём первый элемент
        d = await _get(s, "https://open-api.bingx.com/openApi/spot/v1/ticker/24hr", {"symbol": f"{t}-USDT"})
        # response: {"code":0,"data":[{...}]} или {"code":0,"data":{...}}
        data = d.get("data") or d
        item = data[0] if isinstance(data, list) else data
        last = float(item.get("lastPrice") or item.get("last") or item.get("c"))
        bid  = float(item.get("bidPrice") or item.get("b") or last)
        ask  = float(item.get("askPrice") or item.get("a") or last)
        return PriceResult("BingX", price=last, fair_price=(bid+ask)/2, url=url)
    except Exception as e:
        return PriceResult("BingX", error=str(e)[:100], url=url)
 
 
async def _bitget(s: aiohttp.ClientSession, t: str) -> PriceResult:
    url = chart_url("Bitget", t)
    try:
        d = await _get(s, "https://api.bitget.com/api/v2/spot/market/tickers", {"symbol": f"{t}USDT"})
        item = d["data"][0]
        # Bitget v2 поля: lastPr, bidPr, askPr
        last = float(item.get("lastPr") or item.get("last") or item.get("close"))
        bid  = float(item.get("bidPr") or item.get("buyOne") or item.get("bid") or last)
        ask  = float(item.get("askPr") or item.get("sellOne") or item.get("ask") or last)
        return PriceResult("Bitget", price=last, fair_price=(bid+ask)/2, url=url)
    except Exception as e:
        return PriceResult("Bitget", error=str(e)[:100], url=url)
 
 
async def _gate(s: aiohttp.ClientSession, t: str) -> PriceResult:
    url = chart_url("Gate", t)
    try:
        d = await _get(s, "https://api.gateio.ws/api/v4/spot/tickers", {"currency_pair": f"{t}_USDT"})
        item = d[0]
        last = float(item["last"])
        bid  = float(item.get("highest_bid") or last)
        ask  = float(item.get("lowest_ask") or last)
        return PriceResult("Gate", price=last, fair_price=(bid+ask)/2, url=url)
    except Exception as e:
        return PriceResult("Gate", error=str(e)[:100], url=url)
 
 
# ── DEX — DexScreener ──────────────────────────────────────────────────────────
async def _dexscreener(s: aiohttp.ClientSession, ticker: str) -> PriceResult:
    url = chart_url("DexScreener", ticker)
    if ticker.upper() in CEX_ONLY_TOKENS:
        return PriceResult("DexScreener", error="Только CEX токен, DEX нерелевантен", exchange_type="DEX", url=url)
    try:
        d = await _get(s, f"https://api.dexscreener.com/latest/dex/search?q={ticker}%2FUSDT")
        pairs = d.get("pairs") or []
        matched = [
            p for p in pairs
            if p.get("baseToken", {}).get("symbol", "").upper() == ticker.upper()
            and float((p.get("liquidity") or {}).get("usd", 0) or 0) >= DEX_MIN_LIQ
        ]
        sol = [p for p in matched if p.get("chainId") == "solana"]
        candidates = sol or matched
        if not candidates:
            return PriceResult("DexScreener", error="Пара не найдена (ликвидность < $100k)", exchange_type="DEX", url=url)
        best = max(candidates, key=lambda p: float((p.get("liquidity") or {}).get("usd", 0) or 0))
        pair_addr = best.get("pairAddress", "")
        chain     = best.get("chainId", "solana")
        if pair_addr:
            url = f"https://dexscreener.com/{chain}/{pair_addr}"
        price = float(best["priceUsd"])
        return PriceResult("DexScreener", price=price, fair_price=price, exchange_type="DEX", url=url)
    except asyncio.TimeoutError:
        return PriceResult("DexScreener", error="Таймаут", exchange_type="DEX", url=url)
    except Exception as e:
        return PriceResult("DexScreener", error=str(e)[:80], exchange_type="DEX", url=url)
 
 
# ── DEX — CoinGecko (бесплатный, без ключа) ───────────────────────────────────
# Маппинг тикер → CoinGecko ID
COINGECKO_IDS: dict[str, str] = {
    "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin",
    "SOL": "solana",  "XRP": "ripple",   "ADA": "cardano",
    "DOGE": "dogecoin", "DOT": "polkadot", "MATIC": "matic-network",
    "AVAX": "avalanche-2", "LINK": "chainlink", "UNI": "uniswap",
    "LTC": "litecoin", "BCH": "bitcoin-cash", "ATOM": "cosmos",
    "XLM": "stellar", "NEAR": "near", "FTM": "fantom",
    "ALGO": "algorand", "ICP": "internet-computer", "APT": "aptos",
    "ARB": "arbitrum", "OP": "optimism", "SUI": "sui",
    "TRX": "tron", "TON": "the-open-network", "PEPE": "pepe",
    "WIF": "dogwifcoin", "BONK": "bonk", "JUP": "jupiter-exchange-solana",
    "POPCAT": "popcat", "TRUMP": "official-trump",
}
 
async def _coingecko(s: aiohttp.ClientSession, ticker: str) -> PriceResult:
    cg_id = COINGECKO_IDS.get(ticker.upper())
    url   = f"https://www.coingecko.com/en/coins/{cg_id or ticker.lower()}"
    if not cg_id:
        return PriceResult("CoinGecko", error="ID не найден в маппинге", exchange_type="DEX", url=url)
    try:
        d = await _get(s,
            "https://api.coingecko.com/api/v3/simple/price",
            {"ids": cg_id, "vs_currencies": "usd", "include_24hr_change": "false"}
        )
        price = d.get(cg_id, {}).get("usd")
        if price is None:
            return PriceResult("CoinGecko", error="Нет данных о цене", exchange_type="DEX", url=url)
        return PriceResult("CoinGecko", price=float(price), fair_price=float(price), exchange_type="DEX", url=url)
    except asyncio.TimeoutError:
        return PriceResult("CoinGecko", error="Таймаут", exchange_type="DEX", url=url)
    except Exception as e:
        return PriceResult("CoinGecko", error=str(e)[:80], exchange_type="DEX", url=url)
 
 
# ── Entry point ────────────────────────────────────────────────────────────────
CEX_FETCHERS = [_binance, _bybit, _okx, _mexc, _kucoin, _bingx, _bitget, _gate]
 
async def get_prices_for_ticker(ticker: str) -> list[PriceResult]:
    async with _make_session() as session:
        tasks = (
            [asyncio.create_task(fn(session, ticker)) for fn in CEX_FETCHERS]
            + [
                asyncio.create_task(_dexscreener(session, ticker)),
                asyncio.create_task(_coingecko(session, ticker)),
            ]
        )
        return await asyncio.gather(*tasks)
