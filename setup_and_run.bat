@echo off
setlocal EnableDelayedExpansion
title Laptop Monitor — Auto Setup
color 0B

echo.
echo  ╔══════════════════════════════════════════════════════╗
echo  ║        LAPTOP MONITOR  —  Auto Setup                ║
echo  ║   Installing everything needed. Just wait...        ║
echo  ╚══════════════════════════════════════════════════════╝
echo.

:: ══════════════════════════════════════════
:: STEP 1 — PYTHON
:: ══════════════════════════════════════════
echo  [1/3] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo  [..] Python not found. Downloading Python 3.11...
    echo       This may take 1-2 minutes depending on your internet.
    echo.

    :: curl is built into Windows 10/11
    curl -L -o "%TEMP%\python_installer.exe" "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"

    if errorlevel 1 (
        echo  [ERROR] Could not download Python.
        echo          Check your internet connection and try again.
        pause
        exit /b 1
    )

    echo  [..] Installing Python silently (this takes ~30 seconds)...
    :: /quiet = no UI, PrependPath=1 = adds to PATH automatically
    "%TEMP%\python_installer.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0

    if errorlevel 1 (
        echo  [ERROR] Python installation failed.
        pause
        exit /b 1
    )

    del "%TEMP%\python_installer.exe" >nul 2>&1

    :: Refresh PATH in this session so python works immediately
    for /f "tokens=*" %%i in ('where python 2^>nul') do set "PYTHON_EXE=%%i"
    if "!PYTHON_EXE!"=="" (
        :: Try common install location directly
        set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    )

    echo  [OK] Python installed.
) else (
    for /f "tokens=*" %%i in ('where python') do set "PYTHON_EXE=%%i"
    echo  [OK] Python already installed.
)

:: Use explicit path if found, otherwise just "python"
if "!PYTHON_EXE!"=="" set "PYTHON_EXE=python"


:: ══════════════════════════════════════════
:: STEP 2 — TESSERACT OCR
:: ══════════════════════════════════════════
echo.
echo  [2/3] Checking Tesseract OCR...

:: Check if already installed at default path
if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
    echo  [OK] Tesseract already installed.
    goto :skip_tesseract
)

:: Also check 32-bit path
if exist "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe" (
    echo  [OK] Tesseract already installed.
    goto :skip_tesseract
)

echo  [..] Tesseract not found. Downloading...
echo       This may take 1-2 minutes.
echo.

curl -L -o "%TEMP%\tesseract_installer.exe" "https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe"

if errorlevel 1 (
    echo  [ERROR] Could not download Tesseract.
    echo          Check your internet and try again.
    pause
    exit /b 1
)

echo  [..] Installing Tesseract silently (~20 seconds)...
:: /S = silent install, /D sets install dir
"%TEMP%\tesseract_installer.exe" /S /D=C:\Program Files\Tesseract-OCR

:: Wait a moment for installer to finish
timeout /t 5 >nul

del "%TEMP%\tesseract_installer.exe" >nul 2>&1

if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
    echo  [OK] Tesseract installed.
) else (
    echo  [WARN] Tesseract install may need a moment to complete.
    echo         If OCR doesn't work, restart and run this file again.
)

:skip_tesseract


:: ══════════════════════════════════════════
:: STEP 3 — PYTHON PACKAGES
:: ══════════════════════════════════════════
echo.
echo  [3/3] Installing Python packages...
echo        (First time takes ~1 minute)
echo.

"!PYTHON_EXE!" -m pip install --upgrade pip --quiet

"!PYTHON_EXE!" -m pip install ^
    "Pillow>=10.0.0" ^
    "numpy>=1.24.0" ^
    "flask>=3.0.0" ^
    "pytesseract>=0.3.10" ^
    "pygetwindow>=0.0.9" ^
    "groq>=0.9.0" ^
    "python-dotenv>=1.0.0" ^
    "rake-nltk>=1.0.6" ^
    "nltk>=3.8.0" ^
    "opencv-python>=4.8.0" ^
    --quiet

echo  [..] Downloading NLTK language data...
"!PYTHON_EXE!" -c "import nltk; nltk.download('stopwords',quiet=True); nltk.download('punkt',quiet=True); nltk.download('punkt_tab',quiet=True)"

echo.
echo  ╔══════════════════════════════════════════════════════╗
echo  ║   ✅  Setup complete! Opening the app now...        ║
echo  ╚══════════════════════════════════════════════════════╝
echo.

:: ══════════════════════════════════════════
:: LAUNCH GUI
:: ══════════════════════════════════════════
set "LAUNCHER=%~dp0app_launcher.pyw"

:: Use pythonw so no black console window appears behind the GUI
for /f "tokens=*" %%i in ('where pythonw 2^>nul') do set "PYTHONW_EXE=%%i"
if "!PYTHONW_EXE!"=="" (
    :: Derive pythonw from python path
    set "PYTHONW_EXE=!PYTHON_EXE:python.exe=pythonw.exe!"
)

if exist "!PYTHONW_EXE!" (
    start "" "!PYTHONW_EXE!" "!LAUNCHER!"
) else (
    start "" "!PYTHON_EXE!" "!LAUNCHER!"
)

echo  The app is now open in its own window.
echo  You can close this window.
echo.
timeout /t 4 >nul
