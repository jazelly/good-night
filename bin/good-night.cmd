@echo off
rem Windows launcher: bin\good-night.cmd <command> [options]
rem Uses %GOOD_NIGHT_PYTHON% if set, else the "py -3" launcher, else "python".
setlocal
set "GN=%~dp0good-night"
if defined GOOD_NIGHT_PYTHON goto custom
where py >nul 2>nul
if not errorlevel 1 goto pylauncher
python "%GN%" %*
goto end
:custom
"%GOOD_NIGHT_PYTHON%" "%GN%" %*
goto end
:pylauncher
py -3 "%GN%" %*
:end
exit /b %errorlevel%
