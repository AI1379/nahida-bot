@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
title Nahida Bot - All-in-One Launcher

rem ==============================================================
rem  Nahida Bot 一键启动 / 状态 / 停止
rem
rem  用法：
rem    start-all.bat            启动全部（已运行的组件会自动跳过）
rem    start-all.bat status     只查看各组件状态
rem    start-all.bat stop       停止全部（不会碰你自己的 QQ）
rem ==============================================================

set "BOT_DIR=%~dp0"
if "%BOT_DIR:~-1%"=="\" set "BOT_DIR=%BOT_DIR:~0,-1%"

set "NAPCAT_DIR=D:\NapCat.Shell.Windows.OneKey\NapCat.44498.Shell"
set "QQ_UIN=1801542459"
set "ONEBOT_PORT=3000"
set "WEBUI_PORT=5173"
set "API_PORT=6186"

set "FAILED="
set "MODE=%~1"
if not defined MODE set "MODE=start"

call :detect_api_port
call :banner

if /i "%MODE%"=="start"  goto :do_start
if /i "%MODE%"=="status" goto :do_status
if /i "%MODE%"=="stop"   goto :do_stop
echo [错误] 未知参数 "%MODE%"，可用参数：start / status / stop
goto :do_fail

rem ------------------------------------------------------------
:do_start
call :check_tools
if defined FAILED goto :do_fail
call :start_napcat
call :start_backend
call :start_webui
call :summary
if defined FAILED goto :do_fail
echo.
echo 本窗口即将自动关闭，各服务窗口将继续运行。
call :sleep 3
goto :do_end

rem ------------------------------------------------------------
:do_status
call :summary
echo.
echo 提示：start-all.bat          启动全部
echo       start-all.bat stop     停止全部
goto :do_end

rem ------------------------------------------------------------
:do_stop
echo 正在停止 Nahida Bot 全栈 ...
echo.
echo [1/3] 停止后端（端口 %API_PORT%）...
powershell -NoProfile -Command "Get-NetTCPConnection -State Listen -LocalPort %API_PORT% -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }" >nul 2>&1
taskkill /IM nahida-bot.exe /T /F >nul 2>&1
echo   已处理。

echo [2/3] 停止 WebUI（端口 %WEBUI_PORT%）...
powershell -NoProfile -Command "Get-NetTCPConnection -State Listen -LocalPort %WEBUI_PORT% -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }" >nul 2>&1
echo   已处理。

echo [3/3] 停止 NapCat（仅限 NapCat 目录下的 QQ，不影响你自己的 QQ）...
taskkill /IM NapCatWinBootMain.exe /T /F >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'QQ.exe' -and $_.CommandLine -like '*NapCat.Shell.Windows.OneKey*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
echo   已处理。
echo.
echo 停止完成。
goto :do_end

rem ------------------------------------------------------------
:check_tools
echo [1/4] 检查运行环境 ...
where uv >nul 2>&1
if errorlevel 1 (
    if exist "%USERPROFILE%\.local\bin\uv.exe" (
        set "PATH=%USERPROFILE%\.local\bin;%PATH%"
    ) else (
        echo   [错误] 未找到 uv，请先执行：pip install uv
        set "FAILED=1"
    )
)
where node >nul 2>&1
if errorlevel 1 (
    if exist "D:\node\node.exe" (
        set "PATH=D:\node;%PATH%"
    ) else (
        echo   [错误] 未找到 Node.js
        set "FAILED=1"
    )
)
where pnpm >nul 2>&1
if errorlevel 1 (
    echo   [错误] 未找到 pnpm，请先执行：npm install -g pnpm
    set "FAILED=1"
)
where curl >nul 2>&1
if errorlevel 1 echo   [警告] 未找到 curl.exe，就绪检测将不可用
if defined FAILED goto :eof
echo   环境检查通过。
goto :eof

rem ------------------------------------------------------------
:start_napcat
echo.
echo [2/4] NapCat（QQ %QQ_UIN%，OneBot 端口 %ONEBOT_PORT%）
call :probe http://127.0.0.1:%ONEBOT_PORT%/get_status
if defined UP (
    echo   已在运行，跳过启动。
    goto :eof
)
if not exist "%NAPCAT_DIR%\NapCatWinBootMain.exe" (
    echo   [错误] 找不到 %NAPCAT_DIR%\NapCatWinBootMain.exe
    set "FAILED=1"
    goto :eof
)
echo   正在启动 ...
start "NapCat" /D "%NAPCAT_DIR%" cmd /k "chcp 65001 >nul && NapCatWinBootMain.exe %QQ_UIN%"
call :wait_up http://127.0.0.1:%ONEBOT_PORT%/get_status 45
if defined UP (
    echo   已就绪。
) else (
    echo   [警告] 45 秒内未就绪，请查看 NapCat 窗口。
    set "FAILED=1"
)
goto :eof

rem ------------------------------------------------------------
:start_backend
echo.
echo [3/4] Nahida Bot 后端（WebAPI 端口 %API_PORT%）
call :probe http://127.0.0.1:%API_PORT%/api/status
if defined UP (
    echo   已在运行，跳过启动。
    goto :eof
)
if exist "%BOT_DIR%\.venv\Scripts\nahida-bot.exe" (
    set "BOT_CMD=%BOT_DIR%\.venv\Scripts\nahida-bot.exe start --config-yaml config.yaml"
) else (
    set "BOT_CMD=uv run nahida-bot start --config-yaml config.yaml"
)
echo   正在启动 ...
start "Nahida-Bot-Backend" /D "%BOT_DIR%" cmd /k "chcp 65001 >nul && %BOT_CMD%"
call :wait_up http://127.0.0.1:%API_PORT%/api/status 90
if defined UP (
    echo   已就绪。
) else (
    echo   [警告] 90 秒内未就绪，请查看后端窗口。
    set "FAILED=1"
)
goto :eof

rem ------------------------------------------------------------
:start_webui
echo.
echo [4/4] WebUI 管理界面（端口 %WEBUI_PORT%）
call :probe http://localhost:%WEBUI_PORT%/
if defined UP (
    echo   已在运行，跳过启动。
    call :check_proxy
    goto :eof
)
set "NAHIDA_API_URL=http://127.0.0.1:%API_PORT%"
echo   正在启动 ...（代理后端 %NAHIDA_API_URL%）
start "Nahida-Bot-WebUI" /D "%BOT_DIR%\webui" cmd /k "chcp 65001 >nul && pnpm dev"
call :wait_up http://localhost:%WEBUI_PORT%/ 60
if defined UP (
    echo   已就绪。
    call :check_proxy
) else (
    echo   [警告] 60 秒内未就绪，请查看 WebUI 窗口。
    set "FAILED=1"
)
goto :eof

rem ------------------------------------------------------------
:check_proxy
call :probe http://localhost:%WEBUI_PORT%/api/status
if defined UP (
    echo   WebUI 代理正常（已穿透到后端 %API_PORT%）。
) else (
    echo   [警告] WebUI 代理异常：/api/status 未能穿透到后端 %API_PORT%。
    echo          请在 WebUI 窗口按 Ctrl+C 结束，然后重新执行 start-all.bat。
    set "FAILED=1"
)
goto :eof

rem ------------------------------------------------------------
:summary
echo.
echo ==============================================================
echo   运行状态
echo --------------------------------------------------------------
call :probe http://127.0.0.1:%ONEBOT_PORT%/get_status
if defined UP (echo   NapCat OneBot   [运行中]  http://127.0.0.1:%ONEBOT_PORT%) else (echo   NapCat OneBot   [未运行]  http://127.0.0.1:%ONEBOT_PORT%)
call :probe http://127.0.0.1:%API_PORT%/api/status
if defined UP (echo   后端 WebAPI     [运行中]  http://127.0.0.1:%API_PORT%) else (echo   后端 WebAPI     [未运行]  http://127.0.0.1:%API_PORT%)
call :probe http://localhost:%WEBUI_PORT%/
if defined UP (echo   WebUI           [运行中]  http://localhost:%WEBUI_PORT%) else (echo   WebUI           [未运行]  http://localhost:%WEBUI_PORT%)
call :probe http://localhost:%WEBUI_PORT%/api/status
if defined UP (echo   WebUI 代理      [正常]    后端 %API_PORT% 可达) else (echo   WebUI 代理      [未通过]  后端 %API_PORT% 不可达)
echo --------------------------------------------------------------
echo   WebUI 代理目标：http://127.0.0.1:%API_PORT%
echo ==============================================================
goto :eof

rem ------------------------------------------------------------
:probe
set "UP="
curl.exe -s -m 3 -o nul "%~1" >nul 2>&1
if not errorlevel 1 set "UP=1"
goto :eof

rem ------------------------------------------------------------
:wait_up
set "UP="
set /a "_WAIT_N=0"
:wait_up_loop
curl.exe -s -m 3 -o nul "%~1" >nul 2>&1
if not errorlevel 1 (
    set "UP=1"
    goto :eof
)
set /a "_WAIT_N+=1"
if %_WAIT_N% geq %~2 goto :eof
call :sleep 2
goto :wait_up_loop

rem ------------------------------------------------------------
:detect_api_port
set "RAW_PORT="
for /f "tokens=2 delims=:" %%A in ('findstr /R /C:"^  port: " "%BOT_DIR%\config.yaml" 2^>nul') do set "RAW_PORT=%%A"
if defined RAW_PORT (
    for /f "tokens=1" %%A in ("%RAW_PORT%") do set "API_PORT=%%A"
)
goto :eof

rem ------------------------------------------------------------
:sleep
ping -n %~2 127.0.0.1 >nul
goto :eof

rem ------------------------------------------------------------
:banner
echo ==============================================================
echo    Nahida Bot 一键启动  (NapCat + 后端 + WebUI)
echo ==============================================================
echo    工作目录 : %BOT_DIR%
echo    后端端口 : %API_PORT%
echo    模式     : %MODE%
echo.
goto :eof

rem ------------------------------------------------------------
:do_fail
echo.
echo [失败] 以上有步骤未成功，请检查上面的提示。
pause
goto :do_end

rem ------------------------------------------------------------
:do_end
endlocal
exit /b 0
