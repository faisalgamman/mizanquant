param(
    [switch]$Pipeline,
    [switch]$Scheduler,
    [switch]$NoBrowser,
    [int]$Port = 6910
)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$host.UI.RawUI.WindowTitle = "MizanQuant Server"

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "     MizanQuant - Unified Trading System    " -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# Unset stale system env
$env:ALPACA_SECRET_KEY = $null

# Load .env
$envFile = Join-Path $root ".env"
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        $trimmed = $line.Trim()
        if ($trimmed -eq "" -or $trimmed.StartsWith("#")) { continue }
        $eq = $trimmed.IndexOf("=")
        if ($eq -le 0) { continue }
        $key = $trimmed.Substring(0, $eq).Trim()
        $val = $trimmed.Substring($eq + 1).Trim()
        if ($val.StartsWith('"') -and $val.EndsWith('"')) { $val = $val.Substring(1, $val.Length - 2) }
        Set-Item -Path "env:$key" -Value $val -ErrorAction SilentlyContinue
    }
    Write-Host "  [OK] .env loaded" -ForegroundColor Green
} else {
    Write-Host "  [WARN] .env not found" -ForegroundColor Yellow
}

# PYTHONPATH
$env:PYTHONPATH = "$root;$root\openbb_forecast"
$env:WORKSPACE_HOST = "0.0.0.0"
$env:WORKSPACE_PORT = "$Port"

Write-Host "  Server:     http://localhost:$Port" -ForegroundColor White
Write-Host "  Dashboard:  http://localhost:$Port/static/dashboard.html" -ForegroundColor White
Write-Host "  API docs:   http://localhost:$Port/docs" -ForegroundColor White
Write-Host ""

# Optional: run pipeline once
if ($Pipeline) {
    Write-Host "  Running pipeline (dry-run)..." -ForegroundColor Yellow
    python -c "from app.services.pipeline_orchestrator import run_pipeline; r = run_pipeline(dry_run=True); print(f'  Pipeline: {sum(1 for s in r.stages if s.status==\"ok\")}/{len(r.stages)} stages OK in {r.elapsed_s:.1f}s, signals: {r.signals_passed} passed/{r.signals_executed} executed')"
    Write-Host ""
}

# Optional: start scheduler
if ($Scheduler) {
    Start-Job -Name "Scheduler" -ScriptBlock {
        param($d)
        Set-Location $d
        $env:PYTHONPATH = "$d;$d\openbb_forecast"
        python -c "from app.services.scheduler import start_scheduler; start_scheduler(); import time; [time.sleep(60) for _ in iter(int,1)]"
    } -ArgumentList $root
    Write-Host "  [OK] Scheduler started in background" -ForegroundColor Green
    Write-Host ""
}

# Start server
python -m uvicorn app.workspace_server:app --host 0.0.0.0 --port $Port --reload
