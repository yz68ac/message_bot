@echo off
setlocal
set "VSCODE=%USERPROFILE%\AppData\Local\Programs\Microsoft VS Code\Code.exe"
if exist "%VSCODE%" (
  start "" "%VSCODE%" "%~dp0."
) else (
  echo VS Code was not found. Open this folder in your editor manually.
  pause
)
endlocal
