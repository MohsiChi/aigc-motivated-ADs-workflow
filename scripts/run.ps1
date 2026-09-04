# 启动 API 服务（默认 MOCK 模式，零成本）
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
& "$root\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
exit $LASTEXITCODE
