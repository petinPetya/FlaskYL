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
XRAY_API_SERVER="${XRAY_API_SERVER:-127.0.0.1:10085}"
XRAY_BIN="${XRAY_BIN:-$(command -v xray || true)}"
BUILD_LINK_SCRIPT="${BUILD_LINK_SCRIPT:-$(dirname "$0")/xray-build-vless-link}"

usage() {
    cat <<'EOF'
Usage:
  xray-list-clients.sh [--json]

Lists VLESS clients from the configured Xray inbound and builds a share link
for each client. If Xray API + stats are enabled, the script also returns
traffic usage counters. The script automatically loads `XRAY_ENV_FILE`.
EOF
}

fail() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"
}

stats_supported_in_config() {
    [[ -n "$XRAY_BIN" ]] || return 1
    jq -e '
        .api != null
        and .stats != null
        and (.policy.levels["0"].statsUserUplink // false)
        and (.policy.levels["0"].statsUserDownlink // false)
    ' "$XRAY_CONFIG_PATH" >/dev/null 2>&1
}

query_user_stats() {
    local email="$1"
    local stats_json="{}"

    if ! stats_supported_in_config; then
        jq -n \
            --arg email "$email" \
            '{
                available: false,
                email: $email,
                uplink_bytes: null,
                downlink_bytes: null,
                total_bytes: null
            }'
        return 0
    fi

    stats_json="$("$XRAY_BIN" api statsquery --server="$XRAY_API_SERVER" -pattern "user>>>${email}>>>traffic>>>" 2>/dev/null || true)"

    if [[ -z "$stats_json" ]]; then
        jq -n \
            --arg email "$email" \
            '{
                available: true,
                email: $email,
                uplink_bytes: 0,
                downlink_bytes: 0,
                total_bytes: 0
            }'
        return 0
    fi

    if ! jq -e . >/dev/null 2>&1 <<<"$stats_json"; then
        jq -n \
            --arg email "$email" \
            '{
                available: false,
                email: $email,
                uplink_bytes: null,
                downlink_bytes: null,
                total_bytes: null
            }'
        return 0
    fi

    jq -n \
        --arg email "$email" \
        --argjson stats_payload "$stats_json" '
        reduce ($stats_payload.stat // [])[] as $item (
            {
                available: true,
                email: $email,
                uplink_bytes: 0,
                downlink_bytes: 0,
                total_bytes: 0
            };
            if ($item.name | endswith(">>>uplink")) then
                .uplink_bytes = ($item.value | tonumber)
            elif ($item.name | endswith(">>>downlink")) then
                .downlink_bytes = ($item.value | tonumber)
            else
                .
            end
        ) | .total_bytes = (.uplink_bytes + .downlink_bytes)'
}

AS_JSON=0

while [[ $# -gt 0 ]]; do
    case "$1" in
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

require_command jq
[[ -f "$XRAY_CONFIG_PATH" ]] || fail "Config not found: $XRAY_CONFIG_PATH"
[[ -x "$BUILD_LINK_SCRIPT" ]] || fail "Build link script not found: $BUILD_LINK_SCRIPT"

CLIENTS_JSON="$(
    jq -c --arg tag "$XRAY_INBOUND_TAG" '
        [
            .inbounds[]?
            | select(.tag == $tag)
            | .settings.clients[]?
            | {
                uuid: .id,
                email: (.email // ""),
                flow: (.flow // "")
            }
        ]
    ' "$XRAY_CONFIG_PATH"
)"

ENRICHED_CLIENTS="$(
    jq -cn --argjson clients "$CLIENTS_JSON" '
        $clients | map(. + {
            name: (if (.email // "") != "" then .email else .uuid end),
            link: null,
            stats: {
                available: false,
                uplink_bytes: null,
                downlink_bytes: null,
                total_bytes: null
            }
        })
    '
)"

while IFS= read -r client; do
    [[ -n "$client" ]] || continue
    uuid="$(jq -r '.uuid' <<<"$client")"
    email="$(jq -r '.email' <<<"$client")"
    name="$(jq -r '.name' <<<"$client")"

    link="$("$BUILD_LINK_SCRIPT" --uuid "$uuid" --name "$name" --json | jq -r '.link')"
    stats="$(query_user_stats "$email")"

    ENRICHED_CLIENTS="$(
        jq -cn \
            --argjson clients "$ENRICHED_CLIENTS" \
            --arg uuid "$uuid" \
            --arg link "$link" \
            --argjson stats "$stats" '
            $clients
            | map(
                if .uuid == $uuid then
                    .link = $link
                    | .stats = $stats
                else
                    .
                end
            )
        '
    )"
done < <(jq -c '.[]' <<<"$ENRICHED_CLIENTS")

if [[ "$AS_JSON" == "1" ]]; then
    jq -n \
        --arg status "ok" \
        --arg inbound_tag "$XRAY_INBOUND_TAG" \
        --arg config_path "$XRAY_CONFIG_PATH" \
        --argjson stats_enabled "$(stats_supported_in_config && printf 'true' || printf 'false')" \
        --argjson clients "$ENRICHED_CLIENTS" \
        '{
            status: $status,
            inbound_tag: $inbound_tag,
            config_path: $config_path,
            stats_enabled: $stats_enabled,
            clients: $clients
        }'
else
    jq -r '.[] | "\(.uuid)\t\(.email)\t\(.flow)"' <<<"$ENRICHED_CLIENTS"
fi
