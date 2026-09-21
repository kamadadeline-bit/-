#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "此脚本必须在 macOS 上运行。" >&2
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
FFMPEG_BIN="${FFMPEG_BIN:-}"
ARCH="${ARCH:-$(uname -m)}"
case "$ARCH" in
  arm64|aarch64) ARCH="arm64" ;;
  x86_64|amd64) ARCH="x86_64" ;;
  *) echo "不支持的架构: $ARCH" >&2; exit 1 ;;
esac
PYTHON_ARCH="$($PYTHON_BIN -c 'import platform; print(platform.machine())' 2>/dev/null || true)"
if [[ -n "$PYTHON_ARCH" && "$PYTHON_ARCH" != "$ARCH" ]]; then
  echo "Python 架构 ($PYTHON_ARCH) 与目标架构 ($ARCH) 不一致，请使用对应架构的 Python。" >&2
  exit 1
fi

if [[ -z "$FFMPEG_BIN" ]]; then
  FFMPEG_BIN="$(brew --prefix)/bin"
fi
if [[ ! -x "$FFMPEG_BIN/ffmpeg" || ! -x "$FFMPEG_BIN/ffprobe" ]]; then
  echo "未找到 FFmpeg/ffprobe。请设置 FFMPEG_BIN 为同时包含两者的目录。" >&2
  exit 1
fi

"$PYTHON_BIN" -m pip install --disable-pip-version-check -r requirements.txt pyinstaller

BUILD_ROOT="$ROOT/build/macos/$ARCH"
DIST_ROOT="$BUILD_ROOT/dist"
WORK_ROOT="$BUILD_ROOT/work"
SPEC_ROOT="$BUILD_ROOT/spec"
APP_NAME="KuaishouLiveAssistant"
APP_PATH="$DIST_ROOT/$APP_NAME.app"
RELEASE_ROOT="$ROOT/release"
mkdir -p "$DIST_ROOT" "$WORK_ROOT" "$SPEC_ROOT" "$RELEASE_ROOT"
rm -rf "$APP_PATH"

"$PYTHON_BIN" -m PyInstaller --noconfirm --clean --onedir --windowed \
  --name "$APP_NAME" \
  --osx-bundle-identifier "com.kuaishou.liveassistant" \
  --target-architecture "$ARCH" \
  --distpath "$DIST_ROOT" \
  --workpath "$WORK_ROOT" \
  --specpath "$SPEC_ROOT" \
  app.py

RESOURCE_BIN="$APP_PATH/Contents/Resources/ffmpeg/bin"
RESOURCE_LIB="$APP_PATH/Contents/Resources/ffmpeg/lib"
mkdir -p "$RESOURCE_BIN"
cp -f "$FFMPEG_BIN/ffmpeg" "$FFMPEG_BIN/ffprobe" "$RESOURCE_BIN/"
chmod +x "$RESOURCE_BIN/ffmpeg" "$RESOURCE_BIN/ffprobe"

# Homebrew's binaries are dynamically linked to Cellar/opt paths. Bundle
# those non-system dylibs and rewrite their load commands so the app remains
# usable on a Mac without Homebrew. Static FFmpeg builds simply have nothing
# to copy here.
mkdir -p "$RESOURCE_LIB"
SEEN="|"
bundle_macho() {
  local macho="$1"
  [[ -f "$macho" ]] || return 0
  [[ "$SEEN" == *"|$macho|"* ]] && return 0
  SEEN="${SEEN}${macho}|"
  /usr/bin/install_name_tool -add_rpath "@loader_path/../lib" "$macho" 2>/dev/null || true
  while IFS= read -r dep; do
    [[ -n "$dep" ]] || continue
    case "$dep" in
      @rpath/*)
        local base target candidate
        base="$(basename "$dep")"
        target="$RESOURCE_LIB/$base"
        for candidate in \
          "$FFMPEG_BIN/../lib/$base" \
          "/opt/homebrew/opt/ffmpeg/lib/$base" \
          "/usr/local/opt/ffmpeg/lib/$base" \
          "/opt/homebrew/lib/$base" \
          "/usr/local/lib/$base"; do
          if [[ -f "$candidate" ]]; then
            [[ -f "$target" ]] || cp -L "$candidate" "$target"
            /usr/bin/install_name_tool -change "$dep" "@rpath/$base" "$macho" 2>/dev/null || true
            /usr/bin/install_name_tool -id "@rpath/$base" "$target" 2>/dev/null || true
            bundle_macho "$target"
            break
          fi
        done
        ;;
      /opt/homebrew/*|/usr/local/*|/opt/homebrew/Cellar/*|/usr/local/Cellar/*)
        [[ -f "$dep" ]] || continue
        base="$(basename "$dep")"
        target="$RESOURCE_LIB/$base"
        if [[ ! -f "$target" ]]; then
          cp -L "$dep" "$target"
          chmod 644 "$target"
        fi
        /usr/bin/install_name_tool -change "$dep" "@rpath/$base" "$macho" 2>/dev/null || true
        /usr/bin/install_name_tool -id "@rpath/$base" "$target" 2>/dev/null || true
        bundle_macho "$target"
        ;;
    esac
  done < <(/usr/bin/otool -L "$macho" | tail -n +2 | sed 's/^[[:space:]]*//' | cut -d' ' -f1)
}
bundle_macho "$RESOURCE_BIN/ffmpeg"
bundle_macho "$RESOURCE_BIN/ffprobe"
cp -f README.md "$APP_PATH/Contents/Resources/README.md"

PLIST="$APP_PATH/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :NSCameraUsageDescription string 本软件需要摄像头画面作为直播主画面，并要求主播本人在场" "$PLIST" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Set :NSCameraUsageDescription 本软件需要摄像头画面作为直播主画面，并要求主播本人在场" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :NSMicrophoneUsageDescription string 本软件需要麦克风采集主播的实时互动声音" "$PLIST" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Set :NSMicrophoneUsageDescription 本软件需要麦克风采集主播的实时互动声音" "$PLIST"

cat > "$APP_PATH/Contents/Resources/使用说明.txt" <<'EOF'
快手合规直播助手（macOS）

双击 KuaishouLiveAssistant.app 启动。请在系统设置中允许摄像头和麦克风权限。
必须保持主播本人在场并实时互动；本软件不提供无人值守挂机或规避平台检测功能。
EOF

# Re-sign after modifying the bundle and embedded binaries. This is an
# ad-hoc signature: distribution outside the builder still requires the user
# to approve the app in Gatekeeper, or a Developer ID signature/notarization.
/usr/bin/codesign --force --deep --sign - "$APP_PATH" >/dev/null 2>&1 || true

ZIP_PATH="$RELEASE_ROOT/${APP_NAME}_macOS_${ARCH}.zip"
rm -f "$ZIP_PATH"
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ZIP_PATH"
shasum -a 256 "$ZIP_PATH" > "$ZIP_PATH.sha256"
echo "APP=$APP_PATH"
echo "ZIP=$ZIP_PATH"
