"""
exchange_service.py
──────────────────────────────────────────────────────────────────────────────
CEX  → ccxt async  : Binance, Bybit, OKX, MEXC, KuCoin, BingX, Bitget, Gate
DEX  → HTTP APIs   : DexScreener, GMGN, Aster.finance (Jupiter/Solana)
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

FETCH_TIMEOUT    = 12   # seconds per request
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
    """Fresh exchange instances per request (avoids session-reuse issues)."""
    return {name: cls({"enableRateLimit": True}) for name, cls in CEX_FACTORIES.items()}


# ── Data model ─────────────────────────────────────────────────────────────────
@dataclass
class PriceResult:
    exchange: str
    price: Optional[float] = None       # last / spot price
    fair_price: Optional[float] = None  # (bid + ask) / 2  — for CEX; same as price for DEX
    exchange_type: str = "CEX"          # "CEX" | "DEX"
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
        return PriceResult(exchange=name, error=str(e), exchange_type="CEX")


# ══════════════════════════════════════════════════════════════════════════════
#  DEX — DexScreener
# ══════════════════════════════════════════════════════════════════════════════
async def _fetch_dexscreener(session: aiohttp.ClientSession, ticker: str) -> PriceResult:
    """
    Uses /latest/dex/search endpoint.
    Prefers the Solana pair with the highest USD liquidity.
    """
    url = f"https://api.dexscreener.com/latest/dex/search?q={ticker}%2FUSDT"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with session.get(url, headers=headers, timeout=DEX_HTTP_TIMEOUT) as resp:
            resp.raise_for_status()
            data = await resp.json()

        pairs = data.get("pairs") or []

        # Filter: base token symbol must match, prefer Solana
        matched = [
            p for p in pairs
            if p.get("baseToken", {}).get("symbol", "").upper() == ticker.upper()
        ]
        sol_pairs = [p for p in matched if p.get("chainId") == "solana"]
        candidates = sol_pairs or matched

        if not candidates:
            return PriceResult(exchange="DexScreener", error="Пара не найдена", exchange_type="DEX")

        best = max(
            candidates,
            key=lambda p: float((p.get("liquidity") or {}).get("usd", 0) or 0),
        )
        price_str = best.get("priceUsd")
        if not price_str:
            return PriceResult(exchange="DexScreener", error="Нет priceUsd", exchange_type="DEX")

        price = float(price_str)
        return PriceResult(
            exchange="DexScreener", price=price, fair_price=price, exchange_type="DEX"
        )

    except asyncio.TimeoutError:
        return PriceResult(exchange="DexScreener", error="Таймаут", exchange_type="DEX")
    except Exception as e:
        logger.exception("DexScreener error for %s", ticker)
        return PriceResult(exchange="DexScreener", error=str(e), exchange_type="DEX")


# ══════════════════════════════════════════════════════════════════════════════
#  DEX — GMGN (Solana meme-токены)
# ══════════════════════════════════════════════════════════════════════════════
async def _fetch_gmgn(session: aiohttp.ClientSession, ticker: str) -> PriceResult:
    """
    GMGN Solana quotation API.
    Search by symbol → pick exact match → return price_usd.
    """
    url = "https://gmgn.ai/defi/quotation/v1/tokens/sol/search"
    params  = {"q": ticker, "limit": "5"}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with session.get(url, params=params, headers=headers,
                               timeout=DEX_HTTP_TIMEOUT) as resp:
            resp.raise_for_status()
            data = await resp.json()

        tokens = (data.get("data") or {}).get("tokens") or []
        match = next(
            (t for t in tokens if t.get("symbol", "").upper() == ticker.upper()), None
        )
        if not match:
            return PriceResult(exchange="GMGN", error="Токен не найден", exchange_type="DEX")

        raw_price = match.get("price") or match.get("price_usd")
        if raw_price is None:
            return PriceResult(exchange="GMGN", error="Нет поля price", exchange_type="DEX")

        price = float(raw_price)
        return PriceResult(exchange="GMGN", price=price, fair_price=price, exchange_type="DEX")

    except asyncio.TimeoutError:
        return PriceResult(exchange="GMGN", error="Таймаут", exchange_type="DEX")
    except Exception as e:
        logger.exception("GMGN error for %s", ticker)
        return PriceResult(exchange="GMGN", error=str(e), exchange_type="DEX")


# ══════════════════════════════════════════════════════════════════════════════
#  DEX — Aster.finance  →  Jupiter Price API v6 (Solana)
# ══════════════════════════════════════════════════════════════════════════════
async def _fetch_aster(session: aiohttp.ClientSession, ticker: str) -> PriceResult:
    """
    Aster.finance is a Solana DEX aggregator built on Jupiter routing.
    We resolve the token mint via Jupiter token list, then query Jupiter Price API v6.
    """
    token_list_url = "https://token.jup.ag/all"
    price_url      = "https://price.jup.ag/v6/price"
    headers        = {"User-Agent": "Mozilla/5.0"}

    try:
        # Step 1 — resolve SPL mint address by symbol
        async with session.get(token_list_url, headers=headers,
                               timeout=DEX_HTTP_TIMEOUT) as resp:
            resp.raise_for_status()
            token_list = await resp.json()

        mint: Optional[str] = None
        for t in token_list:
            if t.get("symbol", "").upper() == ticker.upper():
                mint = t.get("address")
                break

        if not mint:
            return PriceResult(
                exchange="Aster", error="Минт не найден в Jupiter token list",
                exchange_type="DEX"
            )

        # Step 2 — get Jupiter best-price
        async with session.get(
            price_url, params={"ids": mint}, headers=headers,
            timeout=DEX_HTTP_TIMEOUT
        ) as resp:
            resp.raise_for_status()
            price_data = await resp.json()

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
        return PriceResult(exchange="Aster", error=str(e), exchange_type="DEX")


# ══════════════════════════════════════════════════════════════════════════════
#  Public entry point
# ══════════════════════════════════════════════════════════════════════════════
async def get_prices_for_ticker(ticker: str) -> list[PriceResult]:
    """
    Fetch prices from all CEX + DEX concurrently.
    Returns list[PriceResult] sorted: CEX first, then DEX.
    """
    symbol   = f"{ticker.upper()}/USDT"
    cex_pool = _make_cex_pool()

    async with aiohttp.ClientSession() as session:
        cex_tasks = [
            asyncio.create_task(_fetch_cex(name, ex, symbol))
            for name, ex in cex_pool.items()
        ]
        dex_tasks = [
            asyncio.create_task(_fetch_dexscreener(session, ticker)),
            asyncio.create_task(_fetch_gmgn(session, ticker)),
            asyncio.create_task(_fetch_aster(session, ticker)),
        ]
        all_results: list[PriceResult] = await asyncio.gather(
            *cex_tasks, *dex_tasks, return_exceptions=False
        )

    # Gracefully close ccxt sessions
    await asyncio.gather(
        *[ex.close() for ex in cex_pool.values()], return_exceptions=True
    )

    return all_results
