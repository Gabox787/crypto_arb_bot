"""
exchange_service.py
──────────────────────────────────────────────────────────────────────────────
CEX  → прямые HTTP запросы к публичным API (без ccxt для проблемных бирж)
DEX  → DexScreener + Birdeye (Solana)
──────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations
 
import asyncio
import logging
from dataclasses import dataclass
from typing import Optional
 
import aiohttp
 
logger = logging.getLogger(__name__)
 
FETCH_TIMEOUT    = 15
HTTP_TIMEOUT     = aiohttp.ClientTimeout(total=FETCH_TIMEOUT)
DEX_MIN_LIQ      = 100_000   # минимальная ликвидность USD для DEX пар
 
 
# ── Chart URLs ─────────────────────────────────────────────────────────────────
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
        "Birdeye":     f"https://birdeye.so/token/{t}?chain=solana",
    }
    return urls.get(exchange, "")
 
 
# ── Data model ─────────────────────────────────────────────────────────────────
@dataclass
class PriceResult:
    exchange: str
    price: Optional[float] = None
    fair_price: Optional[float] = None
    exchange_type: str = "CEX"
    error: Optional[str] = None
    url: str = ""
 
 
# ══════════════════════════════════════════════════════════════════════════════
#  HTTP session
# ══════════════════════════════════════════════════════════════════════════════
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
 
 
# ══════════════════════════════════════════════════════════════════════════════
#  CEX — прямые HTTP запросы
# ══════════════════════════════════════════════════════════════════════════════
 
async def _get_json(session: aiohttp.ClientSession, url: str, params: dict = None) -> dict:
    async with session.get(url, params=params) as resp:
        resp.raise_for_status()
        return await resp.json(content_type=None)
 
 
async def _binance(session: aiohttp.ClientSession, ticker: str) -> PriceResult:
    url = chart_url("Binance", ticker)
    try:
        data = await _get_json(
            session,
            "https://api.binance.com/api/v3/ticker/bookTicker",
            {"symbol": f"{ticker.upper()}USDT"}
        )
        bid = float(data["bidPrice"])
        ask = float(data["askPrice"])
        price = (bid + ask) / 2
        return PriceResult("Binance", price=price, fair_price=price, url=url)
    except Exception as e:
        return PriceResult("Binance", error=str(e)[:100], url=url)
 
 
async def _bybit(session: aiohttp.ClientSession, ticker: str) -> PriceResult:
    url = chart_url("Bybit", ticker)
    try:
        data = await _get_json(
            session,
            "https://api.bybit.com/v5/market/tickers",
            {"category": "spot", "symbol": f"{ticker.upper()}USDT"}
        )
        item = data["result"]["list"][0]
        bid = float(item["bid1Price"])
        ask = float(item["ask1Price"])
        last = float(item["lastPrice"])
        fair = (bid + ask) / 2
        return PriceResult("Bybit", price=last, fair_price=fair, url=url)
    except Exception as e:
        return PriceResult("Bybit", error=str(e)[:100], url=url)
 
 
async def _okx(session: aiohttp.ClientSession, ticker: str) -> PriceResult:
    url = chart_url("OKX", ticker)
    try:
        data = await _get_json(
            session,
            "https://www.okx.com/api/v5/market/ticker",
            {"instId": f"{ticker.upper()}-USDT"}
        )
        item = data["data"][0]
        bid = float(item["bidPx"])
        ask = float(item["askPx"])
        last = float(item["last"])
        fair = (bid + ask) / 2
        return PriceResult("OKX", price=last, fair_price=fair, url=url)
    except Exception as e:
        return PriceResult("OKX", error=str(e)[:100], url=url)
 
 
async def _mexc(session: aiohttp.ClientSession, ticker: str) -> PriceResult:
    url = chart_url("MEXC", ticker)
    try:
        data = await _get_json(
            session,
            "https://api.mexc.com/api/v3/ticker/bookTicker",
            {"symbol": f"{ticker.upper()}USDT"}
        )
        bid = float(data["bidPrice"])
        ask = float(data["askPrice"])
        price = (bid + ask) / 2
        return PriceResult("MEXC", price=price, fair_price=price, url=url)
    except Exception as e:
        return PriceResult("MEXC", error=str(e)[:100], url=url)
 
 
async def _kucoin(session: aiohttp.ClientSession, ticker: str) -> PriceResult:
    url = chart_url("KuCoin", ticker)
    try:
        data = await _get_json(
            session,
            f"https://api.kucoin.com/api/v1/market/orderbook/level1",
            {"symbol": f"{ticker.upper()}-USDT"}
        )
        item = data["data"]
        price = float(item["price"])
        bid = float(item.get("bestBid", price))
        ask = float(item.get("bestAsk", price))
        fair = (bid + ask) / 2 if bid and ask else price
        return PriceResult("KuCoin", price=price, fair_price=fair, url=url)
    except Exception as e:
        return PriceResult("KuCoin", error=str(e)[:100], url=url)
 
 
async def _bingx(session: aiohttp.ClientSession, ticker: str) -> PriceResult:
    url = chart_url("BingX", ticker)
    try:
        data = await _get_json(
            session,
            "https://open-api.bingx.com/openApi/spot/v1/ticker/bookTicker",
            {"symbol": f"{ticker.upper()}-USDT"}
        )
        item = data["data"]
        bid = float(item["bidPrice"])
        ask = float(item["askPrice"])
        price = (bid + ask) / 2
        return PriceResult("BingX", price=price, fair_price=price, url=url)
    except Exception as e:
        return PriceResult("BingX", error=str(e)[:100], url=url)
 
 
async def _bitget(session: aiohttp.ClientSession, ticker: str) -> PriceResult:
    url = chart_url("Bitget", ticker)
    try:
        data = await _get_json(
            session,
            "https://api.bitget.com/api/v2/spot/market/tickers",
            {"symbol": f"{ticker.upper()}USDT"}
        )
        item = data["data"][0]
        bid = float(item["buyOne"])
        ask = float(item["sellOne"])
        last = float(item["lastPr"])
        fair = (bid + ask) / 2
        return PriceResult("Bitget", price=last, fair_price=fair, url=url)
    except Exception as e:
        return PriceResult("Bitget", error=str(e)[:100], url=url)
 
 
async def _gate(session: aiohttp.ClientSession, ticker: str) -> PriceResult:
    url = chart_url("Gate", ticker)
    try:
        data = await _get_json(
            session,
            f"https://api.gateio.ws/api/v4/spot/tickers",
            {"currency_pair": f"{ticker.upper()}_USDT"}
        )
        item = data[0]
        last = float(item["last"])
        bid  = float(item.get("highest_bid", last))
        ask  = float(item.get("lowest_ask", last))
        fair = (bid + ask) / 2
        return PriceResult("Gate", price=last, fair_price=fair, url=url)
    except Exception as e:
        return PriceResult("Gate", error=str(e)[:100], url=url)
 
 
# ══════════════════════════════════════════════════════════════════════════════
#  DEX — DexScreener
# ══════════════════════════════════════════════════════════════════════════════
async def _fetch_dexscreener(session: aiohttp.ClientSession, ticker: str) -> PriceResult:
    url = chart_url("DexScreener", ticker)
    try:
        data = await _get_json(
            session,
            f"https://api.dexscreener.com/latest/dex/search?q={ticker.upper()}%2FUSDT"
        )
        pairs = data.get("pairs") or []
 
        matched = [
            p for p in pairs
            if p.get("baseToken", {}).get("symbol", "").upper() == ticker.upper()
            and float((p.get("liquidity") or {}).get("usd", 0) or 0) >= DEX_MIN_LIQ
        ]
        sol_pairs = [p for p in matched if p.get("chainId") == "solana"]
        candidates = sol_pairs or matched
 
        if not candidates:
            return PriceResult("DexScreener", error="Пара не найдена или ликвидность < $100k", exchange_type="DEX", url=url)
 
        best = max(candidates, key=lambda p: float((p.get("liquidity") or {}).get("usd", 0) or 0))
        pair_addr = best.get("pairAddress", "")
        chain = best.get("chainId", "solana")
        if pair_addr:
            url = f"https://dexscreener.com/{chain}/{pair_addr}"
 
        price_str = best.get("priceUsd")
        if not price_str:
            return PriceResult("DexScreener", error="Нет priceUsd", exchange_type="DEX", url=url)
 
        price = float(price_str)
        return PriceResult("DexScreener", price=price, fair_price=price, exchange_type="DEX", url=url)
 
    except asyncio.TimeoutError:
        return PriceResult("DexScreener", error="Таймаут", exchange_type="DEX", url=url)
    except Exception as e:
        logger.exception("DexScreener error for %s", ticker)
        return PriceResult("DexScreener", error=str(e)[:80], exchange_type="DEX", url=url)
 
 
# ══════════════════════════════════════════════════════════════════════════════
#  DEX — Birdeye (замена GMGN + Aster, не требует авторизации)
# ══════════════════════════════════════════════════════════════════════════════
KNOWN_MINTS: dict[str, str] = {
    "SOL":    "So11111111111111111111111111111111111111112",
    "BTC":    "9n4nbM75f5Ui33ZbPYXn59EwSgE8CGsHtAeTH5YFeJ9E",
    "ETH":    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",
    "USDC":   "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "BONK":   "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "WIF":    "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
    "JUP":    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "POPCAT": "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr",
    "PYTH":   "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3",
    "RAY":    "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
    "ORCA":   "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE",
    "SAMO":   "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",
    "PEPE":   "FdpPGBjMBonzCdE36H3CjDqp7vUfXhb9z8kQ8VHzJGXx",
    "TRUMP":  "6p6xgHyF7AeE6TZkSmFsko444wqoP15icUSqi2jfGiPN",
    "MELANIA":"FUAfBo2jgks6gB4Z4LfZkqSZgzNucisEHqnNebaRxM1P",
}
 
 
async def _fetch_birdeye(session: aiohttp.ClientSession, ticker: str) -> PriceResult:
    """
    Birdeye public price API — no API key needed for basic price.
    Uses /defi/price endpoint with known mint or DexScreener mint lookup.
    """
    url = chart_url("Birdeye", ticker)
    mint = KNOWN_MINTS.get(ticker.upper())
 
    # If not in known mints, try to get from DexScreener pair data
    if not mint:
        try:
            data = await _get_json(
                session,
                f"https://api.dexscreener.com/latest/dex/search?q={ticker.upper()}%2FUSDT"
            )
            pairs = data.get("pairs") or []
            sol_pairs = [
                p for p in pairs
                if p.get("chainId") == "solana"
                and p.get("baseToken", {}).get("symbol", "").upper() == ticker.upper()
                and float((p.get("liquidity") or {}).get("usd", 0) or 0) >= DEX_MIN_LIQ
            ]
            if sol_pairs:
                best = max(sol_pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd", 0) or 0))
                mint = best.get("baseToken", {}).get("address")
        except Exception:
            pass
 
    if not mint:
        return PriceResult("Birdeye", error="Токен не найден на Solana", exchange_type="DEX", url=url)
 
    url = f"https://birdeye.so/token/{mint}?chain=solana"
 
    try:
        data = await _get_json(
            session,
            f"https://public-api.birdeye.so/defi/price",
            {"address": mint}
        )
        price = data.get("data", {}).get("value")
        if not price:
            return PriceResult("Birdeye", error="Нет данных о цене", exchange_type="DEX", url=url)
 
        price = float(price)
        return PriceResult("Birdeye", price=price, fair_price=price, exchange_type="DEX", url=url)
 
    except asyncio.TimeoutError:
        return PriceResult("Birdeye", error="Таймаут", exchange_type="DEX", url=url)
    except Exception as e:
        logger.exception("Birdeye error for %s", ticker)
        return PriceResult("Birdeye", error=str(e)[:80], exchange_type="DEX", url=url)
 
 
# ══════════════════════════════════════════════════════════════════════════════
#  Public entry point
# ══════════════════════════════════════════════════════════════════════════════
CEX_FETCHERS = [_binance, _bybit, _okx, _mexc, _kucoin, _bingx, _bitget, _gate]
 
async def get_prices_for_ticker(ticker: str) -> list[PriceResult]:
    async with _make_session() as session:
        tasks = (
            [asyncio.create_task(fn(session, ticker)) for fn in CEX_FETCHERS]
            + [
                asyncio.create_task(_fetch_dexscreener(session, ticker)),
                asyncio.create_task(_fetch_birdeye(session, ticker)),
            ]
        )
        results: list[PriceResult] = await asyncio.gather(*tasks, return_exceptions=False)
 
    return results
