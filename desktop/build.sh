#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."
PYTHON="${CHAT_RESCUE_PYTHON:-python3.14}"
if [[ ! -x .build-venv/bin/python ]]; then
  "$PYTHON" -m venv .build-venv
  .build-venv/bin/pip install 'pyinstaller>=6.16,<7'
fi
CIPHER="${SQLCIPHER_LIBRARY:-/opt/homebrew/opt/sqlcipher/lib/libsqlcipher.dylib}"
[[ -f "$CIPHER" ]] || { print '缺少 SQLCipher，用 Homebrew 安装或设置 SQLCIPHER_LIBRARY'; exit 1; }
.build-venv/bin/python -m PyInstaller --noconfirm --clean --onedir --name chat-rescue-backend \
  --paths . --hidden-import chat_rescue.__main__ --distpath build/backend-dist --workpath build/pyinstaller \
  --specpath build --add-binary "$CIPHER:." \
  --add-data "$PWD/chat_rescue/vendor/capture_keys.py:chat_rescue/vendor" desktop/backend_entry.py
./desktop/make-icon.sh
APP='dist/Chat Rescue.app'
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
/usr/bin/ditto build/ChatRescue.icns "$APP/Contents/Resources/AppIcon.icns"
xcrun swiftc -parse-as-library -O -target arm64-apple-macosx14.0 desktop/ChatRescue.swift -o "$APP/Contents/MacOS/ChatRescue"
# ditto updates the build-owned backend directory; never includes account data.
/usr/bin/ditto build/backend-dist/chat-rescue-backend "$APP/Contents/Resources/backend"
.build-venv/bin/python - <<'PY'
import plistlib
from pathlib import Path
info={'CFBundleExecutable':'ChatRescue','CFBundleIdentifier':'local.chat-rescue.desktop',
      'CFBundleName':'Chat Rescue','CFBundleDisplayName':'Chat Rescue',
      'CFBundleShortVersionString':'0.3.5','CFBundleVersion':'9','CFBundlePackageType':'APPL',
      'LSMinimumSystemVersion':'14.0','NSHighResolutionCapable':True,
      'NSHumanReadableCopyright':'Chat Rescue contributors · MIT',
      'NSPrincipalClass':'NSApplication','CFBundleIconFile':'AppIcon.icns'}
Path('dist/Chat Rescue.app/Contents/Info.plist').write_bytes(plistlib.dumps(info))
PY
/usr/bin/ditto LICENSE "$APP/Contents/Resources/LICENSE"
/usr/bin/ditto THIRD_PARTY_NOTICES.md "$APP/Contents/Resources/THIRD_PARTY_NOTICES.md"
/usr/bin/codesign --force --deep --sign - "$APP"
/usr/bin/codesign --verify --deep --strict "$APP"
/usr/bin/ditto -c -k --keepParent "$APP" dist/Chat-Rescue-0.3.5-macOS-arm64.zip
print "桌面软件已生成：$PWD/$APP"
