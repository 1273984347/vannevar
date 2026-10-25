@echo off
REM Phase 7.3 Lighthouse 实测 — 验证 p7-3 优化效果（critical CSS 内联 + SSE 延迟 + sw.js 扩充）
REM 使用当前 dev 模式生成的 admin 密码（每次重启服务会变）
REM P0 修复（DRL R1b class-level）：移除硬编码密码，改环境变量注入 + fail-fast
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
set CATS=--only-categories performance --only-categories accessibility --only-categories best-practices --only-categories seo
set CHROME=--chrome-flags="--headless=new --no-sandbox --disable-gpu"

echo === Scanning index: / ===
call lighthouse "%BASE%/" --output json --output html --output-path "%OUTDIR%index-p7" %CHROME% --extra-headers "%AUTH_JSON%" %CATS% --quiet

echo === Scanning demo: /demo ===
call lighthouse "%BASE%/demo" --output json --output html --output-path "%OUTDIR%demo-p7" %CHROME% --extra-headers "%AUTH_JSON%" %CATS% --quiet

echo === Scanning project-detail: /admin/projects/1 ===
call lighthouse "%BASE%/admin/projects/1" --output json --output html --output-path "%OUTDIR%project-detail-p7" %CHROME% --extra-headers "%AUTH_JSON%" %CATS% --quiet

echo === Scanning admin-stats: /admin/stats ===
call lighthouse "%BASE%/admin/stats" --output json --output html --output-path "%OUTDIR%admin-stats-p7" %CHROME% --extra-headers "%AUTH_JSON%" %CATS% --quiet

echo === All scans complete ===
endlocal
