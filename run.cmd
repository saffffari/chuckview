@echo off
rem ChuckView from a source checkout on Windows. Uses .venv beside this file if there is one.
setlocal
set ROOT=%~dp0
cd /d "%ROOT%"
if exist "%ROOT%.venv\Scripts\pythonw.exe" (
    start "" "%ROOT%.venv\Scripts\pythonw.exe" -m chuckview %*
) else (
    start "" pyw -3 -m chuckview %*
)
