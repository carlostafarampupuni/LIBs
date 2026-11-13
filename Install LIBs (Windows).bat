@echo off
rem Double-click to install LIBs. Run it once per computer.
cd /d "%~dp0"
echo Installing LIBs in: %CD%
echo.

where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
%PY% --version >nul 2>nul || goto nopython

%PY% -m venv .venv || goto fail
.venv\Scripts\python -m pip install --upgrade pip || goto fail
.venv\Scripts\python -m pip install -e ".[all]" || goto fail
echo.
echo LIBs is installed. Double-click "Run LIBs (Windows).bat" to analyse your data.
pause
exit /b 0

:nopython
echo Python is not installed.
echo Download it from https://www.python.org/downloads/ and tick "Add python.exe to PATH" during installation.
echo Then double-click this file again.
pause
exit /b 1

:fail
echo.
echo The installation stopped with an error (see above). Check your internet connection and try again.
pause
exit /b 1
