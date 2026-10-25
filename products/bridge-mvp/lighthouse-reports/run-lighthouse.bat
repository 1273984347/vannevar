@echo off
REM Run Lighthouse with Basic Auth - .bat preserves JSON quoting better than PowerShell
REM P0 修复（DRL R1b class-level）：移除硬编码 admin:admin123，改环境变量注入 + fail-fast
setlocal
if "%BRIDGE_ADMIN_PASS%"=="" (
  echo [FATAL] BRIDGE_ADMIN_PASS 环境变量未设置。
  echo         用法: set BRIDGE_ADMIN_PASS=^<your-pass^> ^&^& %~nx0
  exit /b 2
)
for /f "delims=" %%i in ('powershell -NoProfile -Command "[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes('admin:%BRIDGE_ADMIN_PASS%'))"') do set AUTH_B64=%%i
set AUTH_JSON={"Authorization":"Basic %AUTH_B64%"}
set BASE=http://localhost:8000
set OUTDIR=%~dp0

echo === Scanning index: / ===
call lighthouse "%BASE%/" --output json --output html --output-path "%OUTDIR%index" --chrome-flags="--headless=new --no-sandbox --disable-gpu" --extra-headers "%AUTH_JSON%" --only-categories=performance,accessibility,best-practices,seo --quiet

echo === Scanning demo: /demo ===
call lighthouse "%BASE%/demo" --output json --output html --output-path "%OUTDIR%demo" --chrome-flags="--headless=new --no-sandbox --disable-gpu" --extra-headers "%AUTH_JSON%" --only-categories=performance,accessibility,best-practices,seo --quiet

echo === Scanning project-detail: /admin/projects/1 ===
call lighthouse "%BASE%/admin/projects/1" --output json --output html --output-path "%OUTDIR%project-detail" --chrome-flags="--headless=new --no-sandbox --disable-gpu" --extra-headers "%AUTH_JSON%" --only-categories=performance,accessibility,best-practices,seo --quiet

echo === Scanning api-projects-1: /api/projects/1 ===
call lighthouse "%BASE%/api/projects/1" --output json --output html --output-path "%OUTDIR%api-projects-1" --chrome-flags="--headless=new --no-sandbox --disable-gpu" --extra-headers "%AUTH_JSON%" --only-categories=performance,accessibility,best-practices,seo --quiet

echo === All scans complete ===
endlocal
