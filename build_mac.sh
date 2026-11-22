#!/bin/bash
# macOS 포터블 앱 빌드 - python.org 의 Python 3.10 이상 권장 (Homebrew python 은 tkinter 별도 설치 필요: brew install python-tk)
# 결과: dist/WebPConverter-macOS.dmg  (설치 파일: 열어서 앱을 Applications 로 끌어다 놓기)
#       dist/WebPConverter-macOS.zip  (포터블: 압축을 풀어 아무 폴더에서나 실행)
set -e
cd "$(dirname "$0")"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean --windowed --name WebPConverter \
  --osx-bundle-identifier com.local.webpconverter \
  --collect-all tkinterdnd2 --collect-all pillow_heif --collect-all oxipng --collect-all imagequant --collect-all py7zr app.py
mkdir -p dist/pack/WebPConverter-macOS
cp -R dist/WebPConverter.app dist/pack/WebPConverter-macOS/
cp PORTABLE.txt dist/pack/WebPConverter-macOS/사용법.txt
(cd dist/pack && ditto -c -k --keepParent WebPConverter-macOS ../WebPConverter-macOS.zip)
# 설치용 dmg: 앱 + Applications 바로가기 (끌어다 놓기 방식)
mkdir -p dist/dmg
cp -R dist/WebPConverter.app dist/dmg/
ln -s /Applications dist/dmg/Applications
hdiutil create -volname "WebPConverter" -srcfolder dist/dmg -ov -format UDZO dist/WebPConverter-macOS.dmg
rm -rf dist/pack dist/dmg
echo
echo "완료: dist/WebPConverter-macOS.dmg (설치용), dist/WebPConverter-macOS.zip (포터블)"
