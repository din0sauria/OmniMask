$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$be = Join-Path $root "backend"
$fe = Join-Path $root "frontend"

$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) { Write-Host "未找到 uv，请先安装：https://docs.astral.sh/uv/" -ForegroundColor Red; exit 1 }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Write-Host "未找到 Node.js，请先安装" -ForegroundColor Red; exit 1 }

Write-Host "[1/3] 同步后端依赖 ..." -ForegroundColor Cyan
Push-Location $be
& $uv.Source sync --group media
if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Host "后端依赖同步失败" -ForegroundColor Red; exit 1 }
Pop-Location

if (-not (Test-Path (Join-Path $fe "node_modules"))) {
    Write-Host "[2/3] 安装前端依赖 ..." -ForegroundColor Cyan
    Push-Location $fe
    npm install
    if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Host "前端依赖安装失败" -ForegroundColor Red; exit 1 }
    Pop-Location
} else {
    Write-Host "[2/3] 前端依赖已存在，跳过" -ForegroundColor DarkGray
}

Write-Host "[3/3] 启动前后端 ..." -ForegroundColor Cyan
$beProc = Start-Process -FilePath $uv.Source -ArgumentList "run","uvicorn","app.main:app","--host","127.0.0.1","--port","8000" -WorkingDirectory $be -PassThru
$feProc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c","npm run dev" -WorkingDirectory $fe -PassThru

Start-Sleep -Seconds 3
Start-Process "http://localhost:3000"

Write-Host ""
Write-Host "后端  http://127.0.0.1:8000  (PID $($beProc.Id))" -ForegroundColor Green
Write-Host "前端  http://localhost:3000   (PID $($feProc.Id))" -ForegroundColor Green
Write-Host "两个独立窗口保持运行，关闭各自窗口即可停止。" -ForegroundColor DarkGray
