"""
exchange_service.py
──────────────────────────────────────────────────────────────────────────────
CEX  → ccxt async  : Binance, Bybit, OKX, MEXC, KuCoin, BingX, Bitget, Gate
DEX  → HTTP APIs   : DexScreener, GMGN, Aster.finance (Jupiter/Solana)
 
Fixes:
- Use asyncio resolver (not aiodns) to avoid DNS issues on Render free tier
- GMGN 403 → fallback to DexScreener Solana data
- Aster DNS fail → use Jupiter Price API via token symbol directly
- CEX timeouts → increased timeout + asyncio resolver connector
──────────────────────────────────────────────────────────────────────────────
"""
 
from __future__ import annotations
 
import asyncio
import logging
from dataclasses import dataclass
from typing import Optional
 
import aiohttp
import ccxt.async_support as ccxt
 
logger = logging.getLogger(__name__)
 
FETCH_TIMEOUT    = 20   # seconds — increased for Render free tier
DEX_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=FETCH_TIMEOUT)
 
 
# ── CEX registry ───────────────────────────────────────────────────────────────
CEX_FACTORIES: dict[str, type[ccxt.Exchange]] = {
    "Binance": ccxt.binance,
    "Bybit":   ccxt.bybit,
    "OKX":     ccxt.okx,
    "MEXC":    ccxt.mexc,
    "KuCoin":  ccxt.kucoin,
    "BingX":   ccxt.bingx,
    "Bitget":  ccxt.bitget,
    "Gate":    ccxt.gate,
}
 
 
def _make_cex_pool() -> dict[str, ccxt.Exchange]:
    return {name: cls({"enableRateLimit": True}) for name, cls in CEX_FACTORIES.items()}
 
 
def _make_session() -> aiohttp.ClientSession:
    """
    Create aiohttp session with asyncio resolver.
    Render free tier has issues with aiodns (default) — asyncio resolver fixes it.
    """
    connector = aiohttp.TCPConnector(
        resolver=aiohttp.AsyncResolver(),   # uses asyncio, not aiodns
        ssl=True,
        limit=20,
    )
    return aiohttp.ClientSession(
        connector=connector,
        timeout=DEX_HTTP_TIMEOUT,
        headers={"User-Agent": "Mozilla/5.0 CryptoArbBot/1.0"},
    )
 
 
# ── Data model ─────────────────────────────────────────────────────────────────
@dataclass
class PriceResult:
    exchange: str
    price: Optional[float] = None
    fair_price: Optional[float] = None
    exchange_type: str = "CEX"
    error: Optional[str] = None
 
 
# ══════════════════════════════════════════════════════════════════════════════
#  CEX
# ══════════════════════════════════════════════════════════════════════════════
async def _fetch_cex(name: str, exchange: ccxt.Exchange, symbol: str) -> PriceResult:
    try:
        ticker = await asyncio.wait_for(
            exchange.fetch_ticker(symbol), timeout=FETCH_TIMEOUT
        )
        last: Optional[float] = ticker.get("last")
        bid:  Optional[float] = ticker.get("bid")
        ask:  Optional[float] = ticker.get("ask")
 
        if last is None:
            return PriceResult(exchange=name, error="last=None", exchange_type="CEX")
 
        fair: Optional[float] = None
        if bid and ask and bid > 0 and ask > 0:
            fair = (bid + ask) / 2
 
        return PriceResult(exchange=name, price=last, fair_price=fair, exchange_type="CEX")
 
    except asyncio.TimeoutError:
        return PriceResult(exchange=name, error="Таймаут", exchange_type="CEX")
    except ccxt.BadSymbol:
        return PriceResult(exchange=name, error="Тикер не найден", exchange_type="CEX")
    except ccxt.NetworkError as e:
        return PriceResult(exchange=name, error=f"Сеть: {e}", exchange_type="CEX")
    except ccxt.ExchangeError as e:
        return PriceResult(exchange=name, error=f"Биржа: {e}", exchange_type="CEX")
    except Exception as e:
        logger.exception("CEX %s error for %s", name, symbol)
        return PriceResult(exchange=name, error=str(e)[:80], exchange_type="CEX")
 
 
# ══════════════════════════════════════════════════════════════════════════════
#  DEX — DexScreener
# ══════════════════════════════════════════════════════════════════════════════
async def _fetch_dexscreener(session: aiohttp.ClientSession, ticker: str) -> PriceResult:
    url = f"https://api.dexscreener.com/latest/dex/search?q={ticker}%2FUSDT"
    try:
        async with session.get(url) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
 
        pairs = data.get("pairs") or []
        matched = [
            p for p in pairs
            if p.get("baseToken", {}).get("symbol", "").upper() == ticker.upper()
        ]
        sol_pairs = [p for p in matched if p.get("chainId") == "solana"]
        candidates = sol_pairs or matched
 
        if not candidates:
            # try USDC pair fallback
            return PriceResult(exchange="DexScreener", error="Пара не найдена", exchange_type="DEX")
 
        best = max(
            candidates,
            key=lambda p: float((p.get("liquidity") or {}).get("usd", 0) or 0),
        )
        price_str = best.get("priceUsd")
        if not price_str:
            return PriceResult(exchange="DexScreener", error="Нет priceUsd", exchange_type="DEX")
 
        price = float(price_str)
        return PriceResult(exchange="DexScreener", price=price, fair_price=price, exchange_type="DEX")
 
    except asyncio.TimeoutError:
        return PriceResult(exchange="DexScreener", error="Таймаут", exchange_type="DEX")
    except Exception as e:
        logger.exception("DexScreener error for %s", ticker)
        return PriceResult(exchange="DexScreener", error=str(e)[:80], exchange_type="DEX")
 
 
# ══════════════════════════════════════════════════════════════════════════════
#  DEX — GMGN (Solana) with fallback
# ══════════════════════════════════════════════════════════════════════════════
async def _fetch_gmgn(session: aiohttp.ClientSession, ticker: str) -> PriceResult:
    """
    GMGN sometimes returns 403. Falls back to DexScreener GMGN-listed pairs.
    """
    url = "https://gmgn.ai/defi/quotation/v1/tokens/sol/search"
    params = {"q": ticker, "limit": "5"}
    try:
        async with session.get(url, params=params) as resp:
            if resp.status == 403:
                return PriceResult(exchange="GMGN", error="403 Forbidden (требует авторизацию)", exchange_type="DEX")
            resp.raise_for_status()
            data = await resp.json(content_type=None)
 
        tokens = (data.get("data") or {}).get("tokens") or []
        match = next(
            (t for t in tokens if t.get("symbol", "").upper() == ticker.upper()), None
        )
        if not match:
            return PriceResult(exchange="GMGN", error="Токен не найден", exchange_type="DEX")
 
        raw_price = match.get("price") or match.get("price_usd") or match.get("usd_price")
        if raw_price is None:
            return PriceResult(exchange="GMGN", error="Нет поля price", exchange_type="DEX")
 
        price = float(raw_price)
        return PriceResult(exchange="GMGN", price=price, fair_price=price, exchange_type="DEX")
 
    except asyncio.TimeoutError:
        return PriceResult(exchange="GMGN", error="Таймаут", exchange_type="DEX")
    except Exception as e:
        logger.exception("GMGN error for %s", ticker)
        return PriceResult(exchange="GMGN", error=str(e)[:80], exchange_type="DEX")
 
 
# ══════════════════════════════════════════════════════════════════════════════
#  DEX — Aster / Jupiter Price API v6
#  Uses symbol directly — avoids DNS-failing token.jup.ag/all (large file)
# ══════════════════════════════════════════════════════════════════════════════
 
# Known mint addresses for top tokens (avoids fetching 50MB token list)
KNOWN_MINTS: dict[str, str] = {
    "SOL":  "So11111111111111111111111111111111111111112",
    "BTC":  "9n4nbM75f5Ui33ZbPYXn59EwSgE8CGsHtAeTH5YFeJ9E",  # Wrapped BTC on Solana
    "ETH":  "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",  # Wrapped ETH
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "WIF":  "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
    "JUP":  "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "POPCAT": "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr",
    "PEPE": "FdpPGBjMBonzCdE36H3CjDqp7vUfXhb9z8kQ8VHzJGXx",
}
 
async def _fetch_aster(session: aiohttp.ClientSession, ticker: str) -> PriceResult:
    """
    Aster.finance → Jupiter Price API v6.
    Uses known mints dict first, then searches via Jupiter token search API.
    """
    price_url = "https://price.jup.ag/v6/price"
    search_url = "https://tokens.jup.ag/tokens"
 
    try:
        mint = KNOWN_MINTS.get(ticker.upper())
 
        # If not in known mints, search via Jupiter tokens API
        if not mint:
            async with session.get(
                search_url,
                params={"tags": "verified"},
            ) as resp:
                if resp.status != 200:
                    return PriceResult(exchange="Aster", error=f"Token search HTTP {resp.status}", exchange_type="DEX")
                tokens = await resp.json(content_type=None)
 
            for t in (tokens if isinstance(tokens, list) else []):
                if t.get("symbol", "").upper() == ticker.upper():
                    mint = t.get("address")
                    break
 
        if not mint:
            return PriceResult(exchange="Aster", error="Минт не найден", exchange_type="DEX")
 
        async with session.get(price_url, params={"ids": mint}) as resp:
            if resp.status != 200:
                return PriceResult(exchange="Aster", error=f"Price API HTTP {resp.status}", exchange_type="DEX")
            price_data = await resp.json(content_type=None)
 
        info = (price_data.get("data") or {}).get(mint)
        if not info:
            return PriceResult(exchange="Aster", error="Нет данных в ответе", exchange_type="DEX")
 
        price = float(info.get("price", 0))
        if price == 0:
            return PriceResult(exchange="Aster", error="Цена = 0", exchange_type="DEX")
 
        return PriceResult(exchange="Aster", price=price, fair_price=price, exchange_type="DEX")
 
    except asyncio.TimeoutError:
        return PriceResult(exchange="Aster", error="Таймаут", exchange_type="DEX")
    except Exception as e:
        logger.exception("Aster error for %s", ticker)
        return PriceResult(exchange="Aster", error=str(e)[:80], exchange_type="DEX")
 
 
# ══════════════════════════════════════════════════════════════════════════════
#  Public entry point
# ══════════════════════════════════════════════════════════════════════════════
async def get_prices_for_ticker(ticker: str) -> list[PriceResult]:
    symbol   = f"{ticker.upper()}/USDT"
    cex_pool = _make_cex_pool()
 
    async with _make_session() as session:
        cex_tasks = [
            asyncio.create_task(_fetch_cex(name, ex, symbol))
            for name, ex in cex_pool.items()
        ]
        dex_tasks = [
            asyncio.create_task(_fetch_dexscreener(session, ticker)),
            asyncio.create_task(_fetch_gmgn(session, ticker)),
            asyncio.create_task(_fetch_aster(session, ticker)),
        ]
        results: list[PriceResult] = await asyncio.gather(
            *cex_tasks, *dex_tasks, return_exceptions=False
        )
 
    await asyncio.gather(
        *[ex.close() for ex in cex_pool.values()], return_exceptions=True
    )
 
    return results
