# Director Vaggo Bot **4.0.0**

Telegram-бот канала @Vaggo01: заказы, розыгрыши, очередь постов, Grok.

## Быстрый старт (Bothost)

1. Env: `BOT_TOKEN`, `OWNER_USER_IDS`, `CHANNEL_ID=@Vaggo01`
2. Grok: `GROK_BRIDGE_URL` + `GROK_BRIDGE_SECRET` (ПК + tunnel) **или** `XAI_API_KEY`
3. `python bot.py` / Docker
4. `/ping` → `ver: 4.0.0`

## Розыгрыш

- Итог при **min_complete=10** complete-участников
- Если срок вышел и людей меньше — **продление +24ч**
- `/gstatus` · `/gwrestore` · `/gfixkb`

## Локалка

`bot_host_mode=local`, `grok_bridge_disable=true`, Super-сессия на ПК.
Bothost при этом **STOP**.
