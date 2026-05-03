#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${HOME}/Projects/hermes"
CFG_DIR="${HOME}/.config/hermes"
STATE_DIR="${HOME}/.hermes"
LAUNCH_DIR="${HOME}/Library/LaunchAgents"

echo "==> creating dirs"
mkdir -p "${CFG_DIR}" "${STATE_DIR}/logs" "${STATE_DIR}/invoices" "${LAUNCH_DIR}"
chmod 700 "${CFG_DIR}"

echo "==> ensuring uv"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # uv installer puts itself in ~/.local/bin
  export PATH="${HOME}/.local/bin:${PATH}"
fi
uv --version

echo "==> ensuring brew system deps for WeasyPrint"
for pkg in pango cairo gdk-pixbuf glib libffi; do
  brew list "${pkg}" >/dev/null 2>&1 || brew install "${pkg}"
done

echo "==> uv sync"
cd "${PROJECT_DIR}"
uv sync

if [ ! -f "${CFG_DIR}/secrets.toml" ]; then
  echo "==> writing secrets template (mode 600). EDIT THIS FILE before continuing."
  cat > "${CFG_DIR}/secrets.toml" <<'EOF'
[gmail]
imap_user = "dzbeers747@gmail.com"
imap_app_password = "REPLACE_ME"
smtp_user = "dzbeers747@gmail.com"
smtp_app_password = "REPLACE_ME"
from_address = "Dylan Beers <dzbeers747@gmail.com>"
reply_to = "Dylan Beers <dylan@ranger-beers.com>"

[telegram]
bot_token = "REPLACE_ME"
chat_id = 0

[digest]
evening_report = false
EOF
  chmod 600 "${CFG_DIR}/secrets.toml"
  echo "    edit ${CFG_DIR}/secrets.toml then re-run this script"
  exit 0
fi

if grep -q "REPLACE_ME" "${CFG_DIR}/secrets.toml"; then
  echo "==> secrets.toml still has REPLACE_ME placeholders. Edit it and re-run."
  exit 1
fi

echo "==> installing launchd plists"
# Use launchctl load -w (older API) instead of bootstrap. The bootstrap
# command requires a GUI domain which fails over SSH (Input/output error).
# load -w works in any session and persists the plist enabled state.
for plist in ops/launchd/com.beers.hermes-*.plist; do
  name="$(basename "$plist")"
  cp -f "$plist" "${LAUNCH_DIR}/${name}"
  launchctl unload "${LAUNCH_DIR}/${name}" 2>/dev/null || true
  launchctl load -w "${LAUNCH_DIR}/${name}"
  echo "    loaded ${name}"
done

echo "==> done"
echo "logs: ${STATE_DIR}/logs/"
echo "state: ${STATE_DIR}/state.sqlite"
echo "verify: launchctl list | grep com.beers.hermes"
