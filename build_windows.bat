@echo off
chcp 65001 >nul
REM Windows 빌드 - Python 3.10 이상 필요 (python.org 설치본 권장)
REM 결과: dist\WebPConverter-Windows-Setup.exe       (설치 파일 - Inno Setup 이 있을 때)
REM       dist\WebPConverter-Windows-Portable.zip   (포터블 폴더형)
REM       dist\WebPConverter-Windows-SingleFile.exe (포터블 파일 하나)
REM 설치 파일을 만들려면 Inno Setup 6 이 필요합니다:  winget install JRSoftware.InnoSetup
cd /d "%~dp0"
python -m venv .venv || goto :err
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller || goto :err
set OPTS=--noconfirm --clean --windowed --name WebPConverter --collect-all tkinterdnd2 --collect-all pillow_heif --collect-all oxipng --collect-all imagequant --collect-all py7zr

REM 1) 폴더형 (시작이 빠름)
pyinstaller %OPTS% --onedir app.py || goto :err
copy /y PORTABLE.txt dist\WebPConverter\사용법.txt >nul
powershell -NoProfile -Command "Compress-Archive -Force -Path dist\WebPConverter -DestinationPath dist\WebPConverter-Windows-Portable.zip" || goto :err

REM 2) 설치 파일 (Inno Setup 이 설치돼 있을 때만)
set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC goto :noiscc
"%ISCC%" /DAppVersion=1.0.0 installer.iss || goto :err
goto :single
:noiscc
echo.
echo [안내] Inno Setup 이 없어 설치 파일은 건너뜁니다. 설치 파일이 필요하면 아래 명령으로 설치한 뒤 다시 실행하세요.
echo        winget install JRSoftware.InnoSetup
echo.
:single

REM 3) 단일 파일형
pyinstaller %OPTS% --onefile app.py || goto :err
move /y dist\WebPConverter.exe dist\WebPConverter-Windows-SingleFile.exe >nul

echo.
echo 완료: dist 폴더를 확인하세요.
pause
exit /b 0
:err
echo 빌드 실패
pause
exit /b 1
