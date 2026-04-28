# HyperWait Admin Monitor

Polls HyperWait's active waitlists and pings the **HyperWait OPS** Telegram group whenever a guest is waiting too long without being notified.

## Deploy on Replit

1. Create a new Python Repl → "Import from upload" (or drag this folder in)
2. In the **Secrets** tab (lock icon), add:
   - `HYPERWAIT_API_BASE_URL` = `https://hyperwait.com/api/v1/agent`
   - `HYPERWAIT_API_TOKEN` = *(agent token)*
   - `TELEGRAM_BOT_TOKEN` = `8020696236:AAEfbPu4gA7fhnEPYoYFvbQQmD4tKz32KgU`
   - `TELEGRAM_CHAT_ID` = `-5280673666`
   - `HW_WARN_MIN` = `15`
   - `HW_URGENT_MIN` = `30`
   - `HW_POLL_SEC` = `300`
3. Click **Run** — it loops every 5 min.

For 24/7: open the **Deploy** tab → choose **Reserved VM** (background worker, ~$1/mo) → Deploy.
