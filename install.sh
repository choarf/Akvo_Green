#!/usr/bin/env bash
# Installs everything the Akvo Green edge gateway needs to run on a Linux
# device (developed against/for a Raspberry Pi, but works on any
# Debian/Ubuntu-based system with apt): system packages, a Python venv
# with the gateway's own dependencies, the dialout group membership
# needed for /dev/ttyUSB0, AWS IoT Core certificates (prompted for
# interactively), a narrowly-scoped passwordless-sudo rule for `reboot`
# (needed by edge_node_improved.py's _default_reboot_fn - see CLAUDE.md's
# Architecture section for why it reboots the host instead of just
# exiting), and a systemd service to start the gateway on boot.
#
# Usage:
#   ./install.sh              # interactive: prompts for cert paths
#   ./install.sh --skip-certs # skip the cert prompts (add them later, see
#                              # "Next steps" at the end of this run)
#
# Safe to re-run: every step below is idempotent (skips what's already
# done, overwrites generated files like the systemd unit and sudoers rule
# so a re-run always reflects this script's current version of them).
#
# Deliberately does NOT touch config_data/config.json's actual settings
# (gateway_id, city, Modbus port, devices, etc.) - that's yours to edit;
# see the "Next steps" printed at the end.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENKO_GREEN_DIR="$HERE/src/Venko_Green"
VENV_DIR="$HERE/.venv"
CERTS_DIR="$VENKO_GREEN_DIR/certs"
SERVICE_USER="$(whoami)"
SERVICE_NAME="akvo-green"

SKIP_CERTS=0
for arg in "$@"; do
    case "$arg" in
        --skip-certs) SKIP_CERTS=1 ;;
        -h|--help)
            sed -n '2,23p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
            exit 0
            ;;
        *)
            echo "Unknown option: $arg" >&2
            exit 2
            ;;
    esac
done

log() { echo ""; echo "=== $* ==="; }
warn() { echo "WARNING: $*" >&2; }

# ---------------------------------------------------------------------------
# 0. Sanity checks
# ---------------------------------------------------------------------------

log "Checking environment"

if [[ ! -f "$VENKO_GREEN_DIR/edge_node_improved.py" ]]; then
    echo "error: expected to find edge_node_improved.py under $VENKO_GREEN_DIR" >&2
    echo "Run this script from inside the Akvo_Green repo (./install.sh)." >&2
    exit 1
fi

if command -v apt-get >/dev/null 2>&1; then
    APT_AVAILABLE=1
else
    APT_AVAILABLE=0
    warn "apt-get not found - this script targets Debian/Ubuntu/Raspberry Pi OS." \
         " You'll need to install python3-venv/python3-pip/socat yourself; continuing."
fi

IS_PI=0
if [[ -f /proc/device-tree/model ]] && grep -qi "raspberry pi" /proc/device-tree/model 2>/dev/null; then
    IS_PI=1
    echo "Detected: Raspberry Pi ($(cat /proc/device-tree/model))"
else
    echo "Not running on a Raspberry Pi (or couldn't detect one) - continuing anyway."
fi

if [[ "$SERVICE_USER" == "root" ]]; then
    warn "Running as root. The gateway will then run as root too (dialout" \
         " group membership won't be needed, but this is broader privilege" \
         " than necessary for a field device)."
fi

# ---------------------------------------------------------------------------
# 1. System packages
# ---------------------------------------------------------------------------

log "Installing system packages (python3-venv, python3-pip, socat)"

if [[ $APT_AVAILABLE -eq 1 ]]; then
    sudo apt-get update -qq
    sudo apt-get install -y python3-venv python3-pip socat
else
    echo "Skipped (no apt-get)."
fi

# ---------------------------------------------------------------------------
# 2. Python virtual environment + gateway dependencies
# ---------------------------------------------------------------------------

log "Setting up Python venv at ${VENV_DIR#$HERE/}"

if [[ ! -d "$VENV_DIR" ]]; then
    python3 -m venv "$VENV_DIR"
else
    echo "Already exists, reusing it."
fi

REQUIREMENTS_FILE="$VENKO_GREEN_DIR/requirements.txt"
if [[ ! -f "$REQUIREMENTS_FILE" ]]; then
    cat > "$REQUIREMENTS_FILE" <<'REQS'
# Runtime dependencies for edge_node_improved.py / config_manager.py.
# Pin versions here once a known-good combination is confirmed; left
# unpinned for now to match what this repo has been developed against.
pymodbus
pyserial
psutil
awsiotsdk
REQS
    echo "Created $REQUIREMENTS_FILE (didn't exist before)."
fi

echo "Installing into the venv..."
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r "$REQUIREMENTS_FILE" -q
echo "Done. Installed versions:"
"$VENV_DIR/bin/pip" show pymodbus pyserial psutil awsiotsdk 2>/dev/null | grep -E "^(Name|Version):"

# ---------------------------------------------------------------------------
# 3. Serial port access (dialout group)
# ---------------------------------------------------------------------------

log "Checking dialout group membership (needed for /dev/ttyUSB0 access)"

if [[ "$SERVICE_USER" != "root" ]]; then
    if id -nG "$SERVICE_USER" | grep -qw dialout; then
        echo "$SERVICE_USER is already in the dialout group."
    else
        sudo usermod -aG dialout "$SERVICE_USER"
        echo "Added $SERVICE_USER to the dialout group."
        echo "NOTE: this only takes effect on your NEXT login (or run 'newgrp dialout')" \
             " - the systemd service picks it up immediately since it starts a fresh session."
    fi
fi

# ---------------------------------------------------------------------------
# 4. AWS IoT Core certificates
# ---------------------------------------------------------------------------

log "AWS IoT Core certificates"

mkdir -p "$CERTS_DIR"

copy_cert() {
    local label="$1" dest_name="$2"
    local dest="$CERTS_DIR/$dest_name"
    if [[ -f "$dest" ]]; then
        echo "$dest_name already present at $dest - leaving it alone."
        return
    fi
    local src
    read -r -p "Path to your $label (or leave blank to skip): " src
    if [[ -z "$src" ]]; then
        warn "$dest_name not provided - the gateway will retry MQTT forever" \
             " until it's placed at $dest (see 'Next steps' below)."
        return
    fi
    if [[ ! -f "$src" ]]; then
        warn "'$src' doesn't exist - skipping $dest_name."
        return
    fi
    cp "$src" "$dest"
    chmod 600 "$dest"
    echo "Copied to $dest"
}

if [[ $SKIP_CERTS -eq 1 ]]; then
    echo "Skipped (--skip-certs). See 'Next steps' below for where these need to go."
else
    copy_cert "device certificate (cert_filepath)" "certificate.pem.crt"
    copy_cert "private key (pri_key_filepath)" "private.pem.key"
    copy_cert "Amazon Root CA" "AmazonRootCA1.pem"
fi

# ---------------------------------------------------------------------------
# 5. Passwordless sudo for `reboot` (least-privilege - this specific
#    command only, not general sudo access)
# ---------------------------------------------------------------------------

log "Configuring passwordless 'reboot' for $SERVICE_USER"

REBOOT_BIN="$(command -v reboot || echo /usr/sbin/reboot)"
SUDOERS_FILE="/etc/sudoers.d/akvo-green-reboot"
SUDOERS_TMP="$(mktemp)"
echo "$SERVICE_USER ALL=(root) NOPASSWD: $REBOOT_BIN" > "$SUDOERS_TMP"

if sudo visudo -cf "$SUDOERS_TMP" >/dev/null 2>&1; then
    sudo install -m 0440 -o root -g root "$SUDOERS_TMP" "$SUDOERS_FILE"
    echo "Installed $SUDOERS_FILE - $SERVICE_USER can now run '$REBOOT_BIN' without a password."
    echo "(Needed by _default_reboot_fn in edge_node_improved.py - see CLAUDE.md.)"
else
    warn "Generated sudoers rule failed validation - NOT installing it." \
         " _default_reboot_fn will fall back to os._exit(1) instead of a real" \
         " reboot until this is fixed by hand."
fi
rm -f "$SUDOERS_TMP"

# ---------------------------------------------------------------------------
# 6. systemd service
# ---------------------------------------------------------------------------

log "Installing the systemd service"

if command -v systemctl >/dev/null 2>&1; then
    SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
    SERVICE_TMP="$(mktemp)"
    cat > "$SERVICE_TMP" <<EOF
[Unit]
Description=Akvo Green edge gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$VENKO_GREEN_DIR
ExecStart=$VENV_DIR/bin/python3 $VENKO_GREEN_DIR/edge_node_improved.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
    sudo install -m 0644 -o root -g root "$SERVICE_TMP" "$SERVICE_FILE"
    rm -f "$SERVICE_TMP"
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME" >/dev/null 2>&1
    echo "Installed and enabled $SERVICE_FILE (starts on boot; not started now)."
    echo "Restart=on-failure: systemd restarts the process on an unexpected crash," \
         "on top of the gateway's own OS-level reboot escalation for a communication" \
         "layer that stays stuck (see CLAUDE.md's Architecture section)."
else
    warn "systemctl not found - skipping service installation. Run the gateway" \
         " manually: cd $VENKO_GREEN_DIR && $VENV_DIR/bin/python3 edge_node_improved.py"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

log "Done"

echo "Installed:"
echo "  - System packages (python3-venv, python3-pip, socat)"
echo "  - Python venv: ${VENV_DIR#$HERE/}"
echo "  - dialout group membership for $SERVICE_USER"
[[ $SKIP_CERTS -eq 0 ]] && echo "  - AWS IoT certs (whichever you provided) under ${CERTS_DIR#$HERE/}/"
echo "  - Passwordless 'reboot' sudo rule (if validation succeeded above)"
echo "  - systemd service '$SERVICE_NAME' (enabled, not started)"
echo ""
echo "Next steps:"
echo "  1. Edit ${VENKO_GREEN_DIR#$HERE/}/config_data/{devices,modbus,system,aws}.csv" \
     "for your gateway_id/city/Modbus port/sensors, then:"
echo "       cd ${VENKO_GREEN_DIR#$HERE/} && $VENV_DIR/bin/python3 config_manager.py build"
echo "     (or hand-edit config_data/config.json directly - either way it's validated" \
     "against config/schema.py before being used)."
if [[ $SKIP_CERTS -eq 1 ]] || [[ ! -f "$CERTS_DIR/certificate.pem.crt" ]]; then
    echo "  2. Place your AWS IoT certs at:"
    echo "       ${CERTS_DIR#$HERE/}/certificate.pem.crt"
    echo "       ${CERTS_DIR#$HERE/}/private.pem.key"
    echo "       ${CERTS_DIR#$HERE/}/AmazonRootCA1.pem"
fi
echo "  3. If you were just added to the dialout group, log out and back in" \
     "(or 'newgrp dialout') before testing the serial port by hand."
echo "  4. Start the gateway:  sudo systemctl start $SERVICE_NAME"
echo "  5. Check on it:        systemctl status $SERVICE_NAME"
echo "                         journalctl -u $SERVICE_NAME -f"
echo "  6. Before trusting this on real hardware: run the test suite from this repo"
echo "     (./run_tests.sh plan) on a dev machine first - see docs/testing/TEST_PLAN.md."
