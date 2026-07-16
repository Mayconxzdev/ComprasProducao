@echo off
setlocal
cd /d "%~dp0"

if exist "%USERPROFILE%\cv_env\Scripts\python.exe" (
    set "PYTHON_EXE=%USERPROFILE%\cv_env\Scripts\python.exe"
) else (
    set "PYTHON_EXE=python"
)

echo Verificando dependencias principais...
"%PYTHON_EXE%" -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo.
    echo Nao foi possivel instalar/verificar as dependencias automaticamente.
    echo Abra o prompt nesta pasta e rode: python -m pip install -r requirements.txt
    pause
    exit /b 1
)

echo Abrindo Compras Vesper...
"%PYTHON_EXE%" -m app.main
if errorlevel 1 (
    echo.
    echo O aplicativo encontrou um problema ao abrir. Veja o erro acima.
    pause
)
