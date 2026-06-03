import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
 
from exchange_service import get_prices_for_ticker, PriceResult
from keep_alive import start_keep_alive
 
# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)
 
# ── Bot & Dispatcher ────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set!")
 
_raw_owner = os.getenv("OWNER_ID")
if not _raw_owner:
    raise RuntimeError("OWNER_ID environment variable is not set!")
try:
    OWNER_ID = int(_raw_owner)
except ValueError:
    raise RuntimeError("OWNER_ID must be a number (your Telegram user ID)")
 
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()
 
CEX_EXCHANGES = {"Binance", "Bybit", "OKX", "MEXC", "KuCoin", "BingX", "Bitget", "Gate"}
DEX_EXCHANGES = {"DexScreener", "GMGN", "Aster"}
 
 
# ── Access guard ────────────────────────────────────────────────────────────────
def is_owner(message: Message) -> bool:
    return message.from_user is not None and message.from_user.id == OWNER_ID
 
 
async def deny(message: Message) -> None:
    """Silently log and ignore strangers."""
    logger.warning("Unauthorized access attempt from user_id=%s", message.from_user.id)
    # No reply — stranger doesn't even know the bot exists
 
 
# ── Price formatter ─────────────────────────────────────────────────────────────
def fmt_price(price: float) -> str:
    if price >= 1_000:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:.4f}"
    if price >= 0.0001:
        return f"{price:.6f}"
    return f"{price:.10f}"
 
 
# ── Response builder ────────────────────────────────────────────────────────────
def build_response(ticker: str, results: list[PriceResult]) -> str:
    valid  = [r for r in results if r.price is not None]
    errors = [r for r in results if r.price is None]
 
    if not valid:
        error_lines = "\n".join(f"  • {r.exchange}: {r.error}" for r in errors)
        return (
            f"❌ <b>Не удалось получить цены для {ticker.upper()}</b>\n\n"
            f"Ошибки:\n{error_lines}\n\n"
            "Проверь тикер или попробуй позже."
        )
 
    valid.sort(key=lambda r: r.price)
    global_min = valid[0]
    global_max = valid[-1]
    global_spread = ((global_max.price - global_min.price) / global_min.price) * 100
 
    cex_valid = [r for r in valid  if r.exchange_type == "CEX"]
    dex_valid = [r for r in valid  if r.exchange_type == "DEX"]
    cex_err   = [r for r in errors if r.exchange_type == "CEX"]
    dex_err   = [r for r in errors if r.exchange_type == "DEX"]
 
    lines = [f"📊 <b>Арбитраж {ticker.upper()}/USDT</b>\n"]
 
    # ── CEX block ───────────────────────────────────────────────────────────────
    if cex_valid or cex_err:
        lines.append("🏦 <b>CEX</b>")
        for r in cex_valid:
            tag = ""
            if r.exchange == global_min.exchange:
                tag = " 🟢 MIN"
            elif r.exchange == global_max.exchange:
                tag = " 🔴 MAX"
            fair_str = (
                f"  <i>(fair: {fmt_price(r.fair_price)})</i>" if r.fair_price else ""
            )
            chart = f' <a href="{r.url}">📈</a>' if r.url else ""
            lines.append(f"  <code>{r.exchange:<10}</code> ${fmt_price(r.price)}{tag}{fair_str}{chart}")
        for r in cex_err:
            chart = f' <a href="{r.url}">📈</a>' if r.url else ""
            lines.append(f"  <code>{r.exchange:<10}</code> ⚠️ {r.error}{chart}")
 
    # ── DEX block ───────────────────────────────────────────────────────────────
    if dex_valid or dex_err:
        lines.append("\n🔗 <b>DEX (Solana)</b>")        for r in dex_valid:
            tag = ""
            if r.exchange == global_min.exchange:
                tag = " 🟢 MIN"
            elif r.exchange == global_max.exchange:
                tag = " 🔴 MAX"
            chart = f' <a href="{r.url}">📈</a>' if r.url else ""
            lines.append(f"  <code>{r.exchange:<12}</code> ${fmt_price(r.price)}{tag}{chart}")
        for r in dex_err:
            chart = f' <a href="{r.url}">📈</a>' if r.url else ""
            lines.append(f"  <code>{r.exchange:<12}</code> ⚠️ {r.error}{chart}")
 
    # ── CEX-only spread ─────────────────────────────────────────────────────────
    if len(cex_valid) >= 2:
        cex_min = cex_valid[0]   # already sorted
        cex_max = cex_valid[-1]
        cex_spread = ((cex_max.price - cex_min.price) / cex_min.price) * 100
        lines.append(
            f"\n📐 <b>Спред CEX</b>: {cex_spread:.3f}%"
            f"  ({cex_min.exchange} → {cex_max.exchange})"
        )
 
    # ── CEX↔DEX spread ──────────────────────────────────────────────────────────
    if cex_valid and dex_valid:
        lines.append(f"📐 <b>Спред CEX↔DEX</b>: {global_spread:.3f}%")
 
    # ── Global summary ──────────────────────────────────────────────────────────
    lines.append(
        f"\n📉 Минимум: <b>{global_min.exchange}</b> — ${fmt_price(global_min.price)}"
    )
    lines.append(
        f"📈 Максимум: <b>{global_max.exchange}</b> — ${fmt_price(global_max.price)}"
    )
    lines.append(f"📐 Общий спред: <b>{global_spread:.3f}%</b>")
 
    # ── Trade recommendation ────────────────────────────────────────────────────
    if global_spread >= 0.1:
        lines.append(
            f"\n💡 <b>Рекомендация:</b>\n"
            f"   LONG  на <b>{global_min.exchange}</b>"
            f" (покупка по ${fmt_price(global_min.price)})\n"
            f"   SHORT на <b>{global_max.exchange}</b>"
            f" (продажа по ${fmt_price(global_max.price)})"
        )
    else:
        lines.append("\n💤 Спред слишком мал — арбитраж не оправдан.")
 
    return "\n".join(lines)
 
 
# ── Handlers ────────────────────────────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    if not is_owner(message):
        return await deny(message)
    await message.answer(
        "👋 <b>Crypto Arbitrage Bot</b>\n\n"
        "Отправь тикер монеты — я сравню цены на <b>8 CEX + 3 DEX</b>.\n\n"
        "<b>CEX:</b> Binance · Bybit · OKX · MEXC · KuCoin · BingX · Bitget · Gate\n"
        "<b>DEX (Solana):</b> DexScreener · Birdeye\n\n"
        "Примеры: <code>BTC</code>  <code>SOL</code>  <code>WIF</code>  <code>BONK</code>\n\n"
        "/help — справка"
    )
 
 
@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    if not is_owner(message):
        return await deny(message)
    await message.answer(
        "ℹ️ <b>Справка</b>\n\n"
        "Введи тикер монеты (только буквы, 2–10 символов).\n\n"
        "<b>CEX</b> — цена last + справедливая цена (bid+ask)/2\n"
        "<b>DEX</b> — Solana: DexScreener (пары с ликвидностью >$100k) + Birdeye\n\n"
        "Бот покажет:\n"
        "  • Цены по каждой площадке\n"
        "  • Спред внутри CEX\n"
        "  • Спред CEX↔DEX (межрыночный арбитраж)\n"
        "  • Рекомендацию LONG/SHORT\n\n"
        "<b>Порог рекомендации:</b> спред ≥ 0.1%"
    )
 
 
@dp.message(F.text)
async def handle_ticker(message: Message) -> None:
    if not is_owner(message):
        return await deny(message)
 
    raw = message.text.strip()
 
    if not raw.isalpha() or not (2 <= len(raw) <= 10):
        await message.answer(
            "⚠️ Введи корректный тикер (буквы, 2–10 символов).\n"
            "Например: <code>BTC</code>, <code>SOL</code>, <code>WIF</code>"
        )
        return
 
    ticker = raw.upper()
    wait_msg = await message.answer(
        f"⏳ Запрашиваю цены для <b>{ticker}</b> на 8 CEX + 3 DEX…"
    )
 
    try:
        results  = await get_prices_for_ticker(ticker)
        response = build_response(ticker, results)
    except Exception as exc:
        logger.exception("Unexpected error for ticker %s", ticker)
        response = f"❌ Непредвиденная ошибка: {exc}"
 
    await wait_msg.delete()
    await message.answer(response)
 
 
# ── Entry point ─────────────────────────────────────────────────────────────────
async def main() -> None:
    logger.info("Starting bot — 8 CEX + 2 DEX (polling)…")
    start_keep_alive()
    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types(),
        handle_signals=True,
        polling_timeout=30,
    )
 
 
if __name__ == "__main__":
    asyncio.run(main())
