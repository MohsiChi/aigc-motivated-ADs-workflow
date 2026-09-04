# 跑全部测试
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
& "$root\.venv\Scripts\python.exe" -m pytest
exit $LASTEXITCODE
