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
XRAY_LOCK_FILE="${XRAY_LOCK_FILE:-/run/lock/xray-config.lock}"
XRAY_BIN="${XRAY_BIN:-$(command -v xray || true)}"

usage() {
    cat <<'EOF'
Usage:
  xray-remove-client.sh (--uuid <uuid> | --email <email>)
                        [--config <path>] [--service <name>] [--tag <tag>]

Removes a VLESS client from the configured Xray inbound, validates the config,
and restarts the Xray service. Removal is idempotent: if the client does not
exist, the script exits successfully with removed_count = 0. The script
automatically loads `XRAY_ENV_FILE` if present.
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

UUID=""
EMAIL=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --uuid)
            [[ $# -ge 2 ]] || fail "--uuid requires a value"
            UUID="$2"
            shift 2
            ;;
        --email)
            [[ $# -ge 2 ]] || fail "--email requires a value"
            EMAIL="$2"
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

if [[ -n "$UUID" && -n "$EMAIL" ]]; then
    fail "Pass either --uuid or --email, not both"
fi

if [[ -z "$UUID" && -z "$EMAIL" ]]; then
    fail "Pass either --uuid or --email"
fi

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

if [[ -n "$UUID" ]]; then
    REMOVED_COUNT="$(
        jq -r --arg tag "$XRAY_INBOUND_TAG" --arg uuid "$UUID" '
            [
                .inbounds[]?
                | select(.tag == $tag)
                | .settings.clients[]?
                | select(.id == $uuid)
            ] | length
        ' "$XRAY_CONFIG_PATH"
    )"
else
    REMOVED_COUNT="$(
        jq -r --arg tag "$XRAY_INBOUND_TAG" --arg email "$EMAIL" '
            [
                .inbounds[]?
                | select(.tag == $tag)
                | .settings.clients[]?
                | select(.email == $email)
            ] | length
        ' "$XRAY_CONFIG_PATH"
    )"
fi

if [[ "$REMOVED_COUNT" == "0" ]]; then
    jq -n \
        --arg status "ok" \
        --arg selector "${UUID:-$EMAIL}" \
        '{
            status: $status,
            removed_count: 0,
            selector: $selector
        }'
    exit 0
fi

cp -a "$XRAY_CONFIG_PATH" "$BACKUP_PATH"

if [[ -n "$UUID" ]]; then
    jq \
        --arg tag "$XRAY_INBOUND_TAG" \
        --arg uuid "$UUID" \
        '
        (.inbounds[] | select(.tag == $tag) | .settings.clients) |=
        map(select(.id != $uuid))
        ' \
        "$XRAY_CONFIG_PATH" > "$TMP_CONFIG"
else
    jq \
        --arg tag "$XRAY_INBOUND_TAG" \
        --arg email "$EMAIL" \
        '
        (.inbounds[] | select(.tag == $tag) | .settings.clients) |=
        map(select(.email != $email))
        ' \
        "$XRAY_CONFIG_PATH" > "$TMP_CONFIG"
fi

"$XRAY_BIN" run -test -config "$TMP_CONFIG" >/dev/null
cat "$TMP_CONFIG" > "$XRAY_CONFIG_PATH"
schedule_restart

jq -n \
    --arg status "ok" \
    --arg selector_type "$( [[ -n "$UUID" ]] && printf 'uuid' || printf 'email' )" \
    --arg selector_value "${UUID:-$EMAIL}" \
    --argjson removed_count "$REMOVED_COUNT" \
    --arg backup_path "$BACKUP_PATH" \
    '{
        status: $status,
        selector_type: $selector_type,
        selector_value: $selector_value,
        removed_count: $removed_count,
        backup_path: $backup_path
    }'
