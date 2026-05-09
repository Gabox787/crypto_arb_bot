# 🤖 Crypto Arbitrage Bot  
**8 CEX + 3 DEX (Solana)**

Сравнивает цены на 11 площадках и ищет арбитражные возможности.

## Поддерживаемые площадки

| Тип | Биржи |
|-----|-------|
| **CEX** (ccxt) | Binance, Bybit, OKX, MEXC, KuCoin, BingX, Bitget, Gate |
| **DEX** (Solana) | DexScreener, GMGN, Aster.finance (Jupiter routing) |

---

## Быстрый старт (локально)

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export BOT_TOKEN="токен_от_BotFather"
python bot.py
```

---

## Деплой на Render

1. Залей папку в GitHub.
2. Render → **New → Web Service** → выбери репо.
3. `render.yaml` подхватится автоматически.
4. В **Environment** добавь:
   ```
   BOT_TOKEN = <токен>
   ```
5. Зарегистрируй `https://<твой-сервис>.onrender.com/health`  
   в [UptimeRobot](https://uptimerobot.com) (интервал 5 мин) — чтобы Render не засыпал.

---

## Структура проекта

```
crypto_arb_bot/
├── bot.py               # aiogram хэндлеры + форматирование
├── exchange_service.py  # CEX (ccxt async) + DEX (aiohttp)
├── keep_alive.py        # Flask keep-alive на порту 8080
├── requirements.txt
└── render.yaml
```

---

## Как работают цены

**CEX** — `last` (последняя сделка) + `fair_price = (bid + ask) / 2`

**DEX (Solana)**:
- **DexScreener** — агрегирует пары с наибольшей ликвидностью
- **GMGN** — специализируется на Solana meme-токенах
- **Aster.finance** — Jupiter Price API v6 (лучшая цена среди Raydium, Orca, Meteora и др.)

---

## Пример ответа

```
📊 Арбитраж SOL/USDT

🏦 CEX
  Binance     $172.3100 🟢 MIN  (fair: $172.3150)
  Bybit       $172.3500         (fair: $172.3550)
  OKX         $172.3400         (fair: $172.3420)
  MEXC        $172.3600         (fair: $172.3610)
  KuCoin      $172.3300         (fair: $172.3350)
  BingX       $172.3700         (fair: $172.3720)
  Bitget      $172.3800         (fair: $172.3820)
  Gate        $172.3900 🔴 MAX  (fair: $172.3920)

🔗 DEX (Solana)
  DexScreener   $172.3250
  GMGN          $172.3180
  Aster         $172.3210

📐 Спред CEX: 0.046%  (Binance → Gate)
📐 Спред CEX↔DEX: 0.046%

📉 Минимум: Binance — $172.3100
📈 Максимум: Gate — $172.3900
📐 Общий спред: 0.046%

💡 Рекомендация:
   LONG  на Binance (покупка по $172.3100)
   SHORT на Gate    (продажа по $172.3900)
```
