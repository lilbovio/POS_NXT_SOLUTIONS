@echo off
echo Limpiando builds anteriores...
rmdir /s /q build
rmdir /s /q dist

echo Empaquetando la aplicacion...
.\venv\Scripts\pyinstaller --noconfirm --onedir --windowed --add-data "templates;templates/" --add-data "static;static/" --add-data "Base de datos Excel.xlsx;." --name "NXT_POS" "app.py"

echo Proceso terminado. El ejecutable esta en la carpeta dist\NXT_POS
pause
