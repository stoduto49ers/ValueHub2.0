@echo off
cd /d "%~dp0"
echo === Value HUB 2.0 ===

set PYTHON_CMD=python
if exist "%USERPROFILE%\AppData\Local\Programs\Python\Python312\python.exe" (
    set PYTHON_CMD="%USERPROFILE%\AppData\Local\Programs\Python\Python312\python.exe"
)

%PYTHON_CMD% -m valuehub

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERRO] Ocorreu um problema ao executar o ValueHub.
    echo O comando usado foi: %PYTHON_CMD%
)
pause
