#!/usr/bin/env bash
set -euo pipefail

XRAY_ENV_FILE="${XRAY_ENV_FILE:-/etc/lowlands-vpn/xray.env}"

if [[ -f "$XRAY_ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    . "$XRAY_ENV_FILE"
    set +a
fi

XRAY_CONFIG_PATH="${XRAY_CONFIG_PATH:-/usr/local/etc/xray/config.json}"
XRAY_SERVICE_NAME="${XRAY_SERVICE_NAME:-xray}"
XRAY_INBOUND_TAG="${XRAY_INBOUND_TAG:-vless-reality}"
XRAY_FLOW_DEFAULT="${XRAY_FLOW:-xtls-rprx-vision}"
XRAY_LOCK_FILE="${XRAY_LOCK_FILE:-/run/lock/xray-config.lock}"
XRAY_BIN="${XRAY_BIN:-$(command -v xray || true)}"

usage() {
    cat <<'EOF'
Usage:
  xray-add-client.sh --email <email> [--uuid <uuid>] [--name <name>] [--flow <flow>]
                     [--config <path>] [--service <name>] [--tag <tag>]

Adds a VLESS client to the configured Xray inbound, validates the config,
and restarts the Xray service. If --uuid is omitted, the script generates one
with `xray uuid`. The script automatically loads `XRAY_ENV_FILE` if present.
EOF
}

fail() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"
}

schedule_restart() {
    nohup sh -c "sleep 1; systemctl restart '$XRAY_SERVICE_NAME'" >/dev/null 2>&1 &
}

EMAIL=""
UUID=""
NAME="xray-device"
FLOW="$XRAY_FLOW_DEFAULT"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --email)
            [[ $# -ge 2 ]] || fail "--email requires a value"
            EMAIL="$2"
            shift 2
            ;;
        --uuid)
            [[ $# -ge 2 ]] || fail "--uuid requires a value"
            UUID="$2"
            shift 2
            ;;
        --name)
            [[ $# -ge 2 ]] || fail "--name requires a value"
            NAME="$2"
            shift 2
            ;;
        --flow)
            [[ $# -ge 2 ]] || fail "--flow requires a value"
            FLOW="$2"
            shift 2
            ;;
        --config)
            [[ $# -ge 2 ]] || fail "--config requires a value"
            XRAY_CONFIG_PATH="$2"
            shift 2
            ;;
        --service)
            [[ $# -ge 2 ]] || fail "--service requires a value"
            XRAY_SERVICE_NAME="$2"
            shift 2
            ;;
        --tag)
            [[ $# -ge 2 ]] || fail "--tag requires a value"
            XRAY_INBOUND_TAG="$2"
            shift 2
            ;;
        --lock-file)
            [[ $# -ge 2 ]] || fail "--lock-file requires a value"
            XRAY_LOCK_FILE="$2"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            fail "Unknown argument: $1"
            ;;
    esac
done

require_command jq
require_command flock
require_command systemctl
[[ -n "$XRAY_BIN" ]] || fail "xray binary not found; set XRAY_BIN or install xray"
[[ -f "$XRAY_CONFIG_PATH" ]] || fail "Config not found: $XRAY_CONFIG_PATH"
[[ -n "$EMAIL" ]] || fail "--email is required"
[[ "$EMAIL" != *" "* ]] || fail "Email must not contain spaces"

if [[ -z "$UUID" ]]; then
    UUID="$("$XRAY_BIN" uuid)"
fi

[[ "$UUID" != *" "* ]] || fail "UUID must not contain spaces"

LOCK_DIR="$(dirname "$XRAY_LOCK_FILE")"
CONFIG_DIR="$(dirname "$XRAY_CONFIG_PATH")"
TMP_CONFIG="$(mktemp "${CONFIG_DIR}/xray-config.tmp.XXXXXX.json")"
BACKUP_PATH="${XRAY_CONFIG_PATH}.bak.$(date +%Y%m%d%H%M%S)"

cleanup() {
    rm -f "$TMP_CONFIG"
}

trap cleanup EXIT

mkdir -p "$LOCK_DIR"
exec 9>"$XRAY_LOCK_FILE"
flock -x 9

jq -e --arg tag "$XRAY_INBOUND_TAG" '.inbounds[]? | select(.tag == $tag)' "$XRAY_CONFIG_PATH" >/dev/null \
    || fail "Inbound tag not found: $XRAY_INBOUND_TAG"

jq -e --arg tag "$XRAY_INBOUND_TAG" --arg uuid "$UUID" '
    .inbounds[]?
    | select(.tag == $tag)
    | .settings.clients[]?
    | select(.id == $uuid)
' "$XRAY_CONFIG_PATH" >/dev/null && fail "UUID already exists: $UUID"

jq -e --arg tag "$XRAY_INBOUND_TAG" --arg email "$EMAIL" '
    .inbounds[]?
    | select(.tag == $tag)
    | .settings.clients[]?
    | select(.email == $email)
' "$XRAY_CONFIG_PATH" >/dev/null && fail "Email already exists: $EMAIL"

cp -a "$XRAY_CONFIG_PATH" "$BACKUP_PATH"

jq \
    --arg tag "$XRAY_INBOUND_TAG" \
    --arg uuid "$UUID" \
    --arg email "$EMAIL" \
    --arg flow "$FLOW" \
    '
    (.inbounds[] | select(.tag == $tag) | .settings.clients) |=
    ((. // []) + [{"id": $uuid, "email": $email, "flow": $flow}])
    ' \
    "$XRAY_CONFIG_PATH" > "$TMP_CONFIG"

"$XRAY_BIN" run -test -config "$TMP_CONFIG" >/dev/null
cat "$TMP_CONFIG" > "$XRAY_CONFIG_PATH"
LINK="$(
    "$(dirname "$0")/xray-build-vless-link" \
        --uuid "$UUID" \
        --name "$NAME" \
        --json \
    | jq -r '.link'
)"
schedule_restart

jq -n \
    --arg status "ok" \
    --arg uuid "$UUID" \
    --arg email "$EMAIL" \
    --arg name "$NAME" \
    --arg flow "$FLOW" \
    --arg link "$LINK" \
    --arg inbound_tag "$XRAY_INBOUND_TAG" \
    --arg config_path "$XRAY_CONFIG_PATH" \
    --arg backup_path "$BACKUP_PATH" \
    '{
        status: $status,
        uuid: $uuid,
        email: $email,
        name: $name,
        flow: $flow,
        link: $link,
        inbound_tag: $inbound_tag,
        config_path: $config_path,
        backup_path: $backup_path
    }'
