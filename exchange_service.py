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
from dataclasses import dataclass, field
from typing import Optional
 
import aiohttp
import ccxt.async_support as ccxt
 
logger = logging.getLogger(__name__)
 
FETCH_TIMEOUT    = 20
DEX_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=FETCH_TIMEOUT)
 
# Minimum USD liquidity for DEX pairs — filters out illiquid wrapped tokens
DEX_MIN_LIQUIDITY_USD = 50_000
 
 
# ── Chart URLs per exchange ────────────────────────────────────────────────────
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
        "GMGN":        f"https://gmgn.ai/sol/token/{t}",
        "Aster":       f"https://aster.finance/swap?inputMint=So11111111111111111111111111111111111111112&outputMint={t}",
    }
    return urls.get(exchange, "")
 
 
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
    return {
        name: cls({
            "enableRateLimit": True,
            # Skip auto-loading markets on init — avoids heavy exchangeInfo requests
            "options": {"fetchMarkets": False, "loadAllOptions": False},
        })
        for name, cls in CEX_FACTORIES.items()
    }
 
 
def _make_session() -> aiohttp.ClientSession:
    connector = aiohttp.TCPConnector(
        resolver=aiohttp.AsyncResolver(),
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
    url: str = ""
 
 
# ══════════════════════════════════════════════════════════════════════════════
#  CEX
# ══════════════════════════════════════════════════════════════════════════════
async def _fetch_cex(name: str, exchange: ccxt.Exchange, symbol: str, ticker: str) -> PriceResult:
    url = chart_url(name, ticker)
    try:
        data = await asyncio.wait_for(
            exchange.fetch_ticker(symbol), timeout=FETCH_TIMEOUT
        )
        last: Optional[float] = data.get("last")
        bid:  Optional[float] = data.get("bid")
        ask:  Optional[float] = data.get("ask")
 
        if last is None:
            return PriceResult(exchange=name, error="last=None", exchange_type="CEX", url=url)
 
        fair: Optional[float] = None
        if bid and ask and bid > 0 and ask > 0:
            fair = (bid + ask) / 2
 
        return PriceResult(exchange=name, price=last, fair_price=fair, exchange_type="CEX", url=url)
 
    except asyncio.TimeoutError:
        return PriceResult(exchange=name, error="Таймаут", exchange_type="CEX", url=url)
    except ccxt.BadSymbol:
        return PriceResult(exchange=name, error="Тикер не найден", exchange_type="CEX", url=url)
    except ccxt.NetworkError as e:
        # Trim long ccxt network errors (they include full URL + response body)
        short = str(e).split("\n")[0][:120]
        return PriceResult(exchange=name, error=f"Сеть: {short}", exchange_type="CEX", url=url)
    except ccxt.ExchangeError as e:
        short = str(e).split("\n")[0][:120]
        return PriceResult(exchange=name, error=f"Биржа: {short}", exchange_type="CEX", url=url)
    except Exception as e:
        logger.exception("CEX %s error for %s", name, symbol)
        return PriceResult(exchange=name, error=str(e)[:100], exchange_type="CEX", url=url)
 
 
# ══════════════════════════════════════════════════════════════════════════════
#  DEX — DexScreener
# ══════════════════════════════════════════════════════════════════════════════
async def _fetch_dexscreener(session: aiohttp.ClientSession, ticker: str) -> PriceResult:
    url = chart_url("DexScreener", ticker)
    api_url = f"https://api.dexscreener.com/latest/dex/search?q={ticker}%2FUSDT"
    try:
        async with session.get(api_url) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
 
        pairs = data.get("pairs") or []
 
        # Filter: correct base token + minimum liquidity to avoid wrapped/illiquid tokens
        matched = [
            p for p in pairs
            if p.get("baseToken", {}).get("symbol", "").upper() == ticker.upper()
            and float((p.get("liquidity") or {}).get("usd", 0) or 0) >= DEX_MIN_LIQUIDITY_USD
        ]
        sol_pairs = [p for p in matched if p.get("chainId") == "solana"]
        candidates = sol_pairs or matched
 
        if not candidates:
            return PriceResult(exchange="DexScreener", error="Пара не найдена или низкая ликвидность", exchange_type="DEX", url=url)
 
        best = max(candidates, key=lambda p: float((p.get("liquidity") or {}).get("usd", 0) or 0))
 
        # Update URL to exact pair
        pair_addr = best.get("pairAddress", "")
        chain = best.get("chainId", "solana")
        if pair_addr:
            url = f"https://dexscreener.com/{chain}/{pair_addr}"
 
        price_str = best.get("priceUsd")
        if not price_str:
            return PriceResult(exchange="DexScreener", error="Нет priceUsd", exchange_type="DEX", url=url)
 
        price = float(price_str)
        return PriceResult(exchange="DexScreener", price=price, fair_price=price, exchange_type="DEX", url=url)
 
    except asyncio.TimeoutError:
        return PriceResult(exchange="DexScreener", error="Таймаут", exchange_type="DEX", url=url)
    except Exception as e:
        logger.exception("DexScreener error for %s", ticker)
        return PriceResult(exchange="DexScreener", error=str(e)[:80], exchange_type="DEX", url=url)
 
 
# ══════════════════════════════════════════════════════════════════════════════
#  DEX — GMGN
# ══════════════════════════════════════════════════════════════════════════════
async def _fetch_gmgn(session: aiohttp.ClientSession, ticker: str) -> PriceResult:
    url = chart_url("GMGN", ticker)
    api_url = "https://gmgn.ai/defi/quotation/v1/tokens/sol/search"
    params = {"q": ticker, "limit": "5"}
    try:
        async with session.get(api_url, params=params) as resp:
            if resp.status == 403:
                return PriceResult(exchange="GMGN", error="403 Forbidden (только браузер)", exchange_type="DEX", url=url)
            resp.raise_for_status()
            data = await resp.json(content_type=None)
 
        tokens = (data.get("data") or {}).get("tokens") or []
        match = next(
            (t for t in tokens if t.get("symbol", "").upper() == ticker.upper()), None
        )
        if not match:
            return PriceResult(exchange="GMGN", error="Токен не найден", exchange_type="DEX", url=url)
 
        raw_price = match.get("price") or match.get("price_usd") or match.get("usd_price")
        if raw_price is None:
            return PriceResult(exchange="GMGN", error="Нет поля price", exchange_type="DEX", url=url)
 
        # Update URL with actual token address if available
        addr = match.get("address") or match.get("mint")
        if addr:
            url = f"https://gmgn.ai/sol/token/{addr}"
 
        price = float(raw_price)
        return PriceResult(exchange="GMGN", price=price, fair_price=price, exchange_type="DEX", url=url)
 
    except asyncio.TimeoutError:
        return PriceResult(exchange="GMGN", error="Таймаут", exchange_type="DEX", url=url)
    except Exception as e:
        logger.exception("GMGN error for %s", ticker)
        return PriceResult(exchange="GMGN", error=str(e)[:80], exchange_type="DEX", url=url)
 
 
# ══════════════════════════════════════════════════════════════════════════════
#  DEX — Aster / Jupiter Price API v6
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
    "MEME":   "MEMEcZdAvfQ1uiCGkZZuLPqeMHYMhqRqYWHuVFnZ2cE",
    "PEPE":   "FdpPGBjMBonzCdE36H3CjDqp7vUfXhb9z8kQ8VHzJGXx",
    "XRP":    "Ga7W9sJkL3BerNwzxPdkBBqEoAKPSExhfLh8QSZ1pump",  # Solana wrapped XRP (illiquid, expect mismatch)
}
 
 
async def _fetch_aster(session: aiohttp.ClientSession, ticker: str) -> PriceResult:
    url = chart_url("Aster", ticker)
    price_url  = "https://price.jup.ag/v6/price"
    search_url = "https://tokens.jup.ag/tokens"
 
    try:
        mint = KNOWN_MINTS.get(ticker.upper())
 
        if not mint:
            async with session.get(search_url, params={"tags": "verified"}) as resp:
                if resp.status != 200:
                    return PriceResult(exchange="Aster", error=f"Token search HTTP {resp.status}", exchange_type="DEX", url=url)
                tokens = await resp.json(content_type=None)
 
            for t in (tokens if isinstance(tokens, list) else []):
                if t.get("symbol", "").upper() == ticker.upper():
                    mint = t.get("address")
                    break
 
        if not mint:
            return PriceResult(exchange="Aster", error="Минт не найден", exchange_type="DEX", url=url)
 
        # Update Aster URL with mint
        url = f"https://aster.finance/swap?inputMint=So11111111111111111111111111111111111111112&outputMint={mint}"
 
        async with session.get(price_url, params={"ids": mint}) as resp:
            if resp.status != 200:
                return PriceResult(exchange="Aster", error=f"Price API HTTP {resp.status}", exchange_type="DEX", url=url)
            price_data = await resp.json(content_type=None)
 
        info = (price_data.get("data") or {}).get(mint)
        if not info:
            return PriceResult(exchange="Aster", error="Нет данных в ответе", exchange_type="DEX", url=url)
 
        price = float(info.get("price", 0))
        if price == 0:
            return PriceResult(exchange="Aster", error="Цена = 0", exchange_type="DEX", url=url)
 
        return PriceResult(exchange="Aster", price=price, fair_price=price, exchange_type="DEX", url=url)
 
    except asyncio.TimeoutError:
        return PriceResult(exchange="Aster", error="Таймаут", exchange_type="DEX", url=url)
    except Exception as e:
        logger.exception("Aster error for %s", ticker)
        return PriceResult(exchange="Aster", error=str(e)[:80], exchange_type="DEX", url=url)
 
 
# ══════════════════════════════════════════════════════════════════════════════
#  Public entry point
# ══════════════════════════════════════════════════════════════════════════════
async def get_prices_for_ticker(ticker: str) -> list[PriceResult]:
    symbol   = f"{ticker.upper()}/USDT"
    cex_pool = _make_cex_pool()
 
    async with _make_session() as session:
        cex_tasks = [
            asyncio.create_task(_fetch_cex(name, ex, symbol, ticker))
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
