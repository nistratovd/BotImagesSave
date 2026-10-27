#!/usr/bin/env bash
set -euo pipefail
NAME=botimagessave
APP=/opt/botimagessave
CONF=/etc/botimagessave
if [[ $EUID -ne 0 ]]; then echo 'Run with sudo'; exit 1; fi
case "${1:-}" in
install|reinstall)
  SRC=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
  command -v python3 >/dev/null
  python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
  # Install python3-venv using apt separately if absent.
  id "$NAME" >/dev/null 2>&1 || useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin "$NAME"
  systemctl stop "$NAME" 2>/dev/null || true
  install -d -m 755 "$APP"
  install -m 644 "$SRC/bot.py" "$SRC/requirements.txt" "$APP/"
  python3 -m venv "$APP/.venv"
  "$APP/.venv/bin/pip" install --disable-pip-version-check -r "$APP/requirements.txt"
  install -d -o root -g "$NAME" -m 750 "$CONF"
  if [[ ! -f "$CONF/config.toml" ]]; then
    install -o root -g "$NAME" -m 640 "$SRC/config.example.toml" "$CONF/config.toml"
  fi
  install -m 644 "$SRC/scripts/botimagessave.service" /etc/systemd/system/botimagessave.service
  systemctl daemon-reload
  echo "Installed. Configure $CONF/config.toml, then run: sudo bash scripts/manage.sh start"
  ;;
start) systemctl enable --now "$NAME" ;;
stop) systemctl disable --now "$NAME" ;;
restart) systemctl restart "$NAME" ;;
status) systemctl status "$NAME" --no-pager ;;
logs) journalctl -u "$NAME" -n 100 --no-pager ;;
uninstall)
  systemctl disable --now "$NAME" 2>/dev/null || true
  rm -f /etc/systemd/system/botimagessave.service
  systemctl daemon-reload
  rm -rf -- "$APP"
  echo "Removed program. Configuration and credentials retained in $CONF; Local media archive retained in /var/lib/botimagessave (or configured directory)."
  ;;
purge)
  if [[ ${2:-} != --confirm ]]; then echo 'Deletes local configuration. Media archive is retained. Use purge --confirm'; exit 1; fi
  if [[ -e /etc/systemd/system/botimagessave.service ]]; then echo 'Run uninstall first'; exit 1; fi
  rm -rf -- "$CONF"
  echo 'Configuration removed. Media archive and its system user retained to preserve file ownership.'
  ;;
*) echo 'Usage: manage.sh install|reinstall|start|stop|restart|status|logs|uninstall|purge --confirm'; exit 2 ;;
esac
