#!/usr/bin/env bash
# Register Light Table as the default opener for standard 2D image types.
set -eu
here="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
apps_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$apps_dir"
cat > "$apps_dir/2photon-lighttable.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Light Table
Comment=The 2Photon 2D image viewer
Exec=$here/run.sh %f
Icon=$here/lighttable/assets/Lt.svg
Terminal=false
Categories=Graphics;Viewer;
MimeType=image/png;image/jpeg;image/tiff;image/webp;image/bmp;image/gif;
StartupNotify=true
StartupWMClass=2photon-lighttable
EOF
update-desktop-database "$apps_dir" 2>/dev/null || true
for mime in image/png image/jpeg image/tiff image/webp image/bmp image/gif; do
  xdg-mime default 2photon-lighttable.desktop "$mime"
done
echo "Light Table is now the default viewer for PNG/JPEG/TIFF/WebP/BMP/GIF ($here)"
