# Run workspace server with Telegram integration
# ⚠️ Revoke this token via BotFather after use (it was exposed in chat)

$env:TELEGRAM_BOT_TOKEN = "8767138043:AAER2SMYoOZZfNb6HfWCbdwxMI_vQhZ8RPs"
$env:TELEGRAM_CHAT_ID = "5774962001"
$env:WORKSPACE_HOST = "127.0.0.1"
$env:WORKSPACE_PORT = "6910"

Write-Host "Starting OpenBB Forecast Workspace Backend with Telegram..."
Write-Host "Bot: @faisalsalama_bot"
Write-Host "Chat ID: 5774962001"
Write-Host ""
python app/workspace_server.py
