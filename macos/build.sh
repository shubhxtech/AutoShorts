#!/bin/bash
# Builds dist/AutoShorts.app and dist/AutoShorts.dmg
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/dist"
APP="$OUT/AutoShorts.app"
VERSION="$(cat "$ROOT/backend/VERSION")"

echo "→ Building the dashboard"
(cd "$ROOT/frontend" && { [ -d node_modules ] || npm install --no-audit --no-fund; } && npm run build >/dev/null)

echo "→ Compiling the macOS shell"
rm -rf "$APP" && mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
swiftc -O -target arm64-apple-macos13.0 -framework AppKit -framework WebKit -framework UserNotifications \
  "$ROOT/macos/Sources/main.swift" -o "$APP/Contents/MacOS/AutoShorts"

echo "→ Bundling the engine"
mkdir -p "$APP/Contents/Resources/backend"
rsync -a --exclude ".venv" --exclude "__pycache__" --exclude "*.pyc" --exclude "tests" --exclude "*.egg-info" \
  "$ROOT/backend/" "$APP/Contents/Resources/backend/"
rsync -a "$ROOT/frontend/dist/" "$APP/Contents/Resources/web/"
cp "$ROOT/macos/setup.sh" "$APP/Contents/Resources/setup.sh"

echo "→ App icon"
ICONSET="$OUT/AppIcon.iconset"; rm -rf "$ICONSET"; mkdir -p "$ICONSET"
"${PYTHON:-$ROOT/backend/.venv/bin/python}" "$ROOT/macos/make_icon.py" "$ICONSET"
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"; rm -rf "$ICONSET"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>AutoShorts</string>
  <key>CFBundleDisplayName</key><string>AutoShorts</string>
  <key>CFBundleIdentifier</key><string>app.autoshorts.studio</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleExecutable</key><string>AutoShorts</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
  <key>LSApplicationCategoryType</key><string>public.app-category.video</string>
</dict></plist>
PLIST

echo "→ Signing (ad-hoc)"
codesign --force --deep --sign - "$APP" >/dev/null

echo "→ Disk image"
rm -f "$OUT/AutoShorts.dmg"; STAGE="$OUT/dmg"; rm -rf "$STAGE"; mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"; ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "AutoShorts" -srcfolder "$STAGE" -ov -format UDZO "$OUT/AutoShorts.dmg" >/dev/null
rm -rf "$STAGE"
echo "✓ $APP"
echo "✓ $OUT/AutoShorts.dmg"
