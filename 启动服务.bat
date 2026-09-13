@echo off
setlocal
cd /d "%~dp0"

rem ===========================================================================
rem  LawGate demo services - one click launcher (double click this file)
rem
rem  Gradio UI  -> http://127.0.0.1:7860
rem  FastAPI    -> http://127.0.0.1:8010/docs
rem  Close this window (or press Ctrl+C) to stop both services.
rem
rem  ---- THIS FILE MUST STAY PURE ASCII AND USE CRLF LINE ENDINGS ----
rem  Both failure modes below were reproduced on 2026-09-11 (console CP 936):
rem
rem   1) LF-only endings: cmd.exe resumes mid-line and runs the TAIL of each
rem      line, so the window shows errors like 'demo' / 'his' / 'is' / '/d' /
rem      'el' / 'go' / 'rshell' (tails of 'LawGate demo services', 'this',
rem      'cd /d', 'powershell') and the services never start.
rem
rem   2) Non-ASCII literals in a UTF-8 .bat: cmd reads the .bat BYTES in the
rem      console code page, so a UTF-8 script name turns into mojibake and
rem        if exist "%~dp0<script>.ps1"   ->   MISSING
rem      even though the file is right there.
rem
rem  So the launcher script is located by the FIRST CHARACTER OF ITS NAME
rem  (U+542F = 0x542F = 21551) instead of by a literal. That keeps this file
rem  ASCII-only, which works on any code page.
rem  If you rename that .ps1 so it no longer starts with U+542F, change 0x542F.
rem ===========================================================================

set "PSEXE=powershell"
where pwsh >nul 2>nul && set "PSEXE=pwsh"

%PSEXE% -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $s = Get-ChildItem -Filter *.ps1 | Where-Object { [int][char]$_.Name[0] -eq 0x542F } | Select-Object -First 1; if (-not $s) { Write-Host '[ERROR] launcher .ps1 not found in %~dp0' -ForegroundColor Red; exit 1 }; & $s.FullName @args" %*

echo.
echo Services stopped. Press any key to close.
pause >nul
endlocal
