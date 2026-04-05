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
XRAY_INBOUND_TAG="${XRAY_INBOUND_TAG:-vless-reality}"
VLESS_HOST="${VLESS_HOST:-}"
VLESS_PORT="${VLESS_PORT:-443}"
VLESS_PBK="${VLESS_PBK:-}"
VLESS_SNI="${VLESS_SNI:-}"
VLESS_SID="${VLESS_SID:-}"
VLESS_FP="${VLESS_FP:-chrome}"
VLESS_FLOW="${VLESS_FLOW:-xtls-rprx-vision}"
VLESS_TYPE="${VLESS_TYPE:-tcp}"
VLESS_SECURITY="${VLESS_SECURITY:-reality}"
VLESS_ENCRYPTION="${VLESS_ENCRYPTION:-none}"

usage() {
    cat <<'EOF'
Usage:
  xray-build-vless-link.sh --uuid <uuid> [--name <name>]
                           [--host <host>] [--port <port>] [--pbk <public_key>]
                           [--sni <server_name>] [--sid <short_id>] [--fp <fp>]
                           [--flow <flow>] [--json]

Builds a VLESS + REALITY share link from static server parameters and a device UUID.
Most parameters can be provided via environment variables:
  VLESS_HOST VLESS_PORT VLESS_PBK VLESS_SNI VLESS_SID VLESS_FP VLESS_FLOW
The script automatically loads `XRAY_ENV_FILE` if present.
EOF
}

fail() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 1
}

config_has_inbound_tag() {
    [[ -f "$XRAY_CONFIG_PATH" ]] || return 1
    jq -e --arg tag "$XRAY_INBOUND_TAG" '.inbounds[]? | select(.tag == $tag)' "$XRAY_CONFIG_PATH" >/dev/null 2>&1
}

config_allows_short_id() {
    local short_id="$1"
    [[ -f "$XRAY_CONFIG_PATH" ]] || return 1
    jq -e --arg tag "$XRAY_INBOUND_TAG" --arg sid "$short_id" '
        .inbounds[]?
        | select(.tag == $tag)
        | (.streamSettings.realitySettings.shortIds // [])
        | index($sid)
    ' "$XRAY_CONFIG_PATH" >/dev/null 2>&1
}

config_allows_server_name() {
    local server_name="$1"
    [[ -f "$XRAY_CONFIG_PATH" ]] || return 1
    jq -e --arg tag "$XRAY_INBOUND_TAG" --arg server_name "$server_name" '
        .inbounds[]?
        | select(.tag == $tag)
        | (.streamSettings.realitySettings.serverNames // [])
        | index($server_name)
    ' "$XRAY_CONFIG_PATH" >/dev/null 2>&1
}

pick_short_id_from_config() {
    jq -r --arg tag "$XRAY_INBOUND_TAG" '
        first(
            .inbounds[]?
            | select(.tag == $tag)
            | (.streamSettings.realitySettings.shortIds // []) as $ids
            | (($ids | map(select(length > 0)) | .[0]) // ($ids[0] // empty))
        )
    ' "$XRAY_CONFIG_PATH"
}

pick_server_name_from_config() {
    jq -r --arg tag "$XRAY_INBOUND_TAG" '
        first(
            .inbounds[]?
            | select(.tag == $tag)
            | (.streamSettings.realitySettings.serverNames[0] // empty)
        )
    ' "$XRAY_CONFIG_PATH"
}

hydrate_reality_values_from_config() {
    config_has_inbound_tag || return 0

    if [[ -z "$VLESS_SNI" ]] || ! config_allows_server_name "$VLESS_SNI"; then
        VLESS_SNI="$(pick_server_name_from_config)"
    fi

    if [[ -z "$VLESS_SID" ]] || ! config_allows_short_id "$VLESS_SID"; then
        VLESS_SID="$(pick_short_id_from_config)"
    fi
}

urlencode() {
    jq -nr --arg value "$1" '$value|@uri'
}

UUID=""
NAME="xray-device"
AS_JSON=0

while [[ $# -gt 0 ]]; do
    case "$1" in
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
        --host)
            [[ $# -ge 2 ]] || fail "--host requires a value"
            VLESS_HOST="$2"
            shift 2
            ;;
        --port)
            [[ $# -ge 2 ]] || fail "--port requires a value"
            VLESS_PORT="$2"
            shift 2
            ;;
        --pbk)
            [[ $# -ge 2 ]] || fail "--pbk requires a value"
            VLESS_PBK="$2"
            shift 2
            ;;
        --sni)
            [[ $# -ge 2 ]] || fail "--sni requires a value"
            VLESS_SNI="$2"
            shift 2
            ;;
        --sid)
            [[ $# -ge 2 ]] || fail "--sid requires a value"
            VLESS_SID="$2"
            shift 2
            ;;
        --fp)
            [[ $# -ge 2 ]] || fail "--fp requires a value"
            VLESS_FP="$2"
            shift 2
            ;;
        --flow)
            [[ $# -ge 2 ]] || fail "--flow requires a value"
            VLESS_FLOW="$2"
            shift 2
            ;;
        --json)
            AS_JSON=1
            shift
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

hydrate_reality_values_from_config

[[ -n "$UUID" ]] || fail "--uuid is required"
[[ -n "$VLESS_HOST" ]] || fail "VLESS_HOST or --host is required"
[[ -n "$VLESS_PBK" ]] || fail "VLESS_PBK or --pbk is required"
[[ -n "$VLESS_SNI" ]] || fail "VLESS_SNI or --sni is required"
if [[ -z "$VLESS_SID" ]] && ! config_allows_short_id ""; then
    fail "VLESS_SID or --sid is required"
fi

UUID_ENCODED="$(urlencode "$UUID")"
PBK_ENCODED="$(urlencode "$VLESS_PBK")"
SNI_ENCODED="$(urlencode "$VLESS_SNI")"
SID_ENCODED="$(urlencode "$VLESS_SID")"
FP_ENCODED="$(urlencode "$VLESS_FP")"
FLOW_ENCODED="$(urlencode "$VLESS_FLOW")"
TYPE_ENCODED="$(urlencode "$VLESS_TYPE")"
SECURITY_ENCODED="$(urlencode "$VLESS_SECURITY")"
ENCRYPTION_ENCODED="$(urlencode "$VLESS_ENCRYPTION")"
NAME_ENCODED="$(urlencode "$NAME")"

LINK="vless://${UUID_ENCODED}@${VLESS_HOST}:${VLESS_PORT}?type=${TYPE_ENCODED}&security=${SECURITY_ENCODED}&pbk=${PBK_ENCODED}&fp=${FP_ENCODED}&sni=${SNI_ENCODED}&sid=${SID_ENCODED}&flow=${FLOW_ENCODED}&encryption=${ENCRYPTION_ENCODED}#${NAME_ENCODED}"

if [[ "$AS_JSON" == "1" ]]; then
    jq -n \
        --arg link "$LINK" \
        --arg uuid "$UUID" \
        --arg name "$NAME" \
        --arg host "$VLESS_HOST" \
        --arg port "$VLESS_PORT" \
        --arg pbk "$VLESS_PBK" \
        --arg sni "$VLESS_SNI" \
        --arg sid "$VLESS_SID" \
        --arg fp "$VLESS_FP" \
        --arg flow "$VLESS_FLOW" \
        '{
            link: $link,
            uuid: $uuid,
            name: $name,
            host: $host,
            port: $port,
            pbk: $pbk,
            sni: $sni,
            sid: $sid,
            fp: $fp,
            flow: $flow
        }'
else
    printf '%s\n' "$LINK"
fi
