@echo off
rem Double-click to analyse every batch in a project and open the figures.
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist ".venv\Scripts\libs.exe" (
  echo LIBs is not installed yet. Double-click "Install LIBs (Windows).bat" first.
  pause
  exit /b 1
)

rem Every folder here that holds notebooks\config.yaml is a project.
set count=0
for /d %%D in (*) do (
  if exist "%%D\notebooks\config.yaml" (
    set /a count+=1
    set "p!count!=%%D"
    echo   !count!. %%D
  )
)

if %count%==0 (
  echo No project found next to this file.
  pause
  exit /b 1
)
if %count%==1 (
  set "project=!p1!"
) else (
  set /p choice=Which project? Type its number and press Enter:
  for %%N in (!choice!) do set "project=!p%%N!"
)

echo.
echo Analysing: !project!
echo.
.venv\Scripts\libs run "!project!" --open
echo.
pause
