@echo off
cd /d "%~dp0"

echo Iniciando Watios Server en esta carpeta:
echo %CD%
echo.
echo Cierra esta ventana solo si quieres apagar el servidor.
echo Si el servidor se cae por error, se intentara reiniciar.
echo.

:loop
where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python Servidor.py
) else (
  py Servidor.py
)

echo.
echo El servidor se detuvo. Reintentando en 3 segundos...
timeout /t 3 /nobreak >nul
goto loop
