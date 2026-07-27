@echo off
cd /d "%~dp0"
echo === Value HUB 2.0 ===

REM Auto-detecta o Python (funciona no desktop E no notebook, sem reeditar):
REM   1) conda env nfl_env (este PC)  2) Python312 do sistema (notebook)
REM   3) python no PATH   4) launcher py
REM As checagens de caminho vem PRIMEIRO de proposito: no desktop o "python"
REM puro cai no atalho quebrado da Microsoft Store, entao evitamos ele.
set "PYTHON_CMD="
if exist "%USERPROFILE%\.conda\envs\nfl_env\python.exe" set "PYTHON_CMD=%USERPROFILE%\.conda\envs\nfl_env\python.exe"
if not defined PYTHON_CMD if exist "%USERPROFILE%\AppData\Local\Programs\Python\Python312\python.exe" set "PYTHON_CMD=%USERPROFILE%\AppData\Local\Programs\Python\Python312\python.exe"
if not defined PYTHON_CMD where python >nul 2>nul && set "PYTHON_CMD=python"
if not defined PYTHON_CMD where py >nul 2>nul && set "PYTHON_CMD=py"

if not defined PYTHON_CMD (
    echo.
    echo [ERRO] Python nao encontrado. Instale o Python ou ajuste o caminho no run.bat.
    pause
    exit /b 1
)

echo Usando Python: %PYTHON_CMD%
echo Abra http://localhost:8000 no navegador.
echo.
"%PYTHON_CMD%" -m valuehub

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERRO] Ocorreu um problema ao executar o ValueHub.
    echo O comando usado foi: %PYTHON_CMD%
)
pause
