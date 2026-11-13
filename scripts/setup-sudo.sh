#!/bin/sh
# One-time setup: allow the current user to run exactly
#   sudo /sbin/shutdown -h now
# without a password, and nothing else.
#
#   ./scripts/setup-sudo.sh            install the rule (asks for your password once)
#   ./scripts/setup-sudo.sh --uninstall
#
# The rule goes in its own drop-in file, /etc/sudoers.d/good-night, and is
# syntax-checked with `visudo -c` before it is installed, so a typo can never
# break sudo. /etc/sudoers itself is not touched.
set -eu

RULE_FILE=/etc/sudoers.d/good-night
USER_NAME="${SUDO_USER:-$(id -un)}"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "This script is for macOS." >&2
  exit 1
fi

if [ "${1:-}" = "--uninstall" ]; then
  sudo rm -f "$RULE_FILE"
  echo "Removed $RULE_FILE"
  exit 0
fi

case "$USER_NAME" in
  root) echo "Run this as your normal user, not root (it will call sudo itself)." >&2; exit 1 ;;
  *[!A-Za-z0-9._-]*) echo "Unexpected user name: $USER_NAME" >&2; exit 1 ;;
esac

# macOS ships with "#includedir /private/etc/sudoers.d" in /etc/sudoers.
if ! sudo grep -Eq '^[#@]includedir[[:space:]]+(/private)?/etc/sudoers\.d' /etc/sudoers; then
  echo "Your /etc/sudoers does not include /etc/sudoers.d; add the rule with 'sudo visudo' instead (see README)." >&2
  exit 1
fi

TMP=$(mktemp -t good-night-sudoers)
trap 'rm -f "$TMP"' EXIT
cat > "$TMP" <<RULE
# Installed by good-night (scripts/setup-sudo.sh).
# Lets $USER_NAME run exactly "/sbin/shutdown -h now" without a password.
$USER_NAME ALL=(root) NOPASSWD: /sbin/shutdown -h now
RULE

sudo /usr/sbin/visudo -c -q -f "$TMP"
sudo install -m 0440 -o root -g wheel "$TMP" "$RULE_FILE"
sudo /usr/sbin/visudo -c -q
echo "Installed $RULE_FILE:"
sudo cat "$RULE_FILE"

if sudo -k && sudo -n -l /sbin/shutdown -h now >/dev/null 2>&1; then
  echo "OK: 'sudo -n /sbin/shutdown -h now' works without a password."
else
  echo "Rule installed, but 'sudo -n -l /sbin/shutdown -h now' still fails; check 'sudo -l'." >&2
  exit 1
fi
