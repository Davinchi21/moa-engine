# ─── MoA Engine — установка ───

Write-Host "╔══════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║   MoA Engine — Mixture of Agents     ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════╝" -ForegroundColor Cyan

# 1. Ставим зависимости
Write-Host "`n[1/3] Устанавливаю зависимости..." -ForegroundColor Yellow
pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Host "Ошибка установки зависимостей" -ForegroundColor Red; exit 1 }

# 2. Создаём .env если нет
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "`n[2/3] Создан .env — вставь свои API ключи!" -ForegroundColor Yellow
    Write-Host "  Редактируй файл: .env" -ForegroundColor White
} else {
    Write-Host "`n[2/3] .env уже существует" -ForegroundColor Green
}

# 3. Проверяем конфиг
Write-Host "`n[3/3] Проверяю конфигурацию..." -ForegroundColor Yellow
python main.py --status

Write-Host "`n╔══════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║   MoA Engine готов к работе!          ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host "`nЗапуск:"
Write-Host "  python main.py ""твой запрос""          # CLI"
Write-Host "  python main.py --web                   # Web UI на http://127.0.0.1:7888"
Write-Host "  python main.py --mcp                    # MCP-сервер для OpenCode" -ForegroundColor White
