@echo off
setlocal EnableExtensions

rem ---- PREFIX from Constructor; fallback if run by hand ----
if not defined PREFIX set "PREFIX=%~dp0"

rem ---- PowerShell path (simple) ----
set "PS=%WINDIR%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=powershell.exe"
set "PSFLAGS=-NoLogo -NoProfile -ExecutionPolicy Bypass"

rem ---- 1) Unpack PyInstaller bundle (ignore errors) ----
if exist "%PREFIX%\payload.zip" (
  "%PS%" %PSFLAGS% -Command ^
    "Expand-Archive -LiteralPath '%PREFIX%\payload.zip' -DestinationPath '%PREFIX%' -Force" ^
    >nul 2>&1
)

rem ---- 2) Create shortcuts (ignore errors) ----
if exist "%PREFIX%\create_shortcuts.ps1" (
  "%PS%" %PSFLAGS% -File "%PREFIX%\create_shortcuts.ps1" -Prefix "%PREFIX%" >nul 2>&1
)

rem ---- 3) Prepend our GenTL path (user-scope only; no admin) ----
set "MYGENTL=%PREFIX%\gentl"
if exist "%MYGENTL%" (
  echo %GENICAM_GENTL64_PATH% | find /i "%MYGENTL%" >nul || (
    if defined GENICAM_GENTL64_PATH (
      set "NEWG=%MYGENTL%;%GENICAM_GENTL64_PATH%"
    ) else (
      set "NEWG=%MYGENTL%"
    )
    setx GENICAM_GENTL64_PATH "%NEWG%" >nul 2>&1
  )
)

rem ---- Always succeed so Constructor doesn't bail ----
exit /b 0
