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
BUILD_LINK_SCRIPT="${BUILD_LINK_SCRIPT:-$(dirname "$0")/xray-build-vless-link}"

usage() {
    cat <<'EOF'
Использование:
  xray-update-client-email.sh --uuid <uuid> --email <new_email> [--name <name>] [--json]
                              [--config <path>] [--service <name>] [--tag <tag>]

Обновляет email (label) VLESS-клиента в настроенном inbound Xray по UUID.
UUID клиента не меняется, поэтому VLESS-ссылка остаётся прежней.
Если существует `XRAY_ENV_FILE`, скрипт загрузит его автоматически.
EOF
}

fail() {
    printf 'ОШИБКА: %s\n' "$1" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "Не найдена обязательная команда: $1"
}

schedule_restart() {
    nohup sh -c "sleep 1; systemctl restart '$XRAY_SERVICE_NAME'" >/dev/null 2>&1 &
}

UUID=""
EMAIL=""
NAME=""
AS_JSON=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --uuid)
            [[ $# -ge 2 ]] || fail "Для --uuid нужно указать значение"
            UUID="$2"
            shift 2
            ;;
        --email)
            [[ $# -ge 2 ]] || fail "Для --email нужно указать значение"
            EMAIL="$2"
            shift 2
            ;;
        --name)
            [[ $# -ge 2 ]] || fail "Для --name нужно указать значение"
            NAME="$2"
            shift 2
            ;;
        --json)
            AS_JSON=1
            shift
            ;;
        --config)
            [[ $# -ge 2 ]] || fail "Для --config нужно указать значение"
            XRAY_CONFIG_PATH="$2"
            shift 2
            ;;
        --service)
            [[ $# -ge 2 ]] || fail "Для --service нужно указать значение"
            XRAY_SERVICE_NAME="$2"
            shift 2
            ;;
        --tag)
            [[ $# -ge 2 ]] || fail "Для --tag нужно указать значение"
            XRAY_INBOUND_TAG="$2"
            shift 2
            ;;
        --lock-file)
            [[ $# -ge 2 ]] || fail "Для --lock-file нужно указать значение"
            XRAY_LOCK_FILE="$2"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            fail "Неизвестный аргумент: $1"
            ;;
    esac
done

require_command jq
require_command flock
require_command systemctl
[[ -n "$XRAY_BIN" ]] || fail "Бинарник xray не найден; задай XRAY_BIN или установи xray"
[[ -f "$XRAY_CONFIG_PATH" ]] || fail "Конфиг не найден: $XRAY_CONFIG_PATH"
[[ -x "$BUILD_LINK_SCRIPT" ]] || fail "Скрипт сборки ссылки не найден: $BUILD_LINK_SCRIPT"
[[ -n "$UUID" ]] || fail "Нужно указать --uuid"
[[ -n "$EMAIL" ]] || fail "Нужно указать --email"
[[ "$UUID" != *" "* ]] || fail "UUID не должен содержать пробелы"
[[ "$EMAIL" != *" "* ]] || fail "Email не должен содержать пробелы"

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
    || fail "Inbound tag не найден: $XRAY_INBOUND_TAG"

CLIENT_STATE="$(
    jq -c --arg tag "$XRAY_INBOUND_TAG" --arg uuid "$UUID" '
        first(
            .inbounds[]?
            | select(.tag == $tag)
            | .settings.clients[]?
            | select(.id == $uuid)
            | {
                found: true,
                email: (.email // "")
            }
        ) // {}
    ' "$XRAY_CONFIG_PATH"
)"
CURRENT_EMAIL="$(jq -r '.email // ""' <<<"$CLIENT_STATE")"
CLIENT_FOUND="$(jq -r '.found // false' <<<"$CLIENT_STATE")"

if [[ "$CLIENT_FOUND" != "true" ]]; then
    fail "Клиент с UUID не найден: $UUID"
fi

if [[ "$CURRENT_EMAIL" != "$EMAIL" ]]; then
    jq -e --arg tag "$XRAY_INBOUND_TAG" --arg email "$EMAIL" '
        .inbounds[]?
        | select(.tag == $tag)
        | .settings.clients[]?
        | select(.email == $email)
    ' "$XRAY_CONFIG_PATH" >/dev/null && fail "Email уже существует: $EMAIL"

    cp -a "$XRAY_CONFIG_PATH" "$BACKUP_PATH"

    jq \
        --arg tag "$XRAY_INBOUND_TAG" \
        --arg uuid "$UUID" \
        --arg email "$EMAIL" \
        '
        (.inbounds[] | select(.tag == $tag) | .settings.clients) |=
        map(
            if .id == $uuid then
                .email = $email
            else
                .
            end
        )
        ' \
        "$XRAY_CONFIG_PATH" > "$TMP_CONFIG"

    "$XRAY_BIN" run -test -config "$TMP_CONFIG" >/dev/null
    cat "$TMP_CONFIG" > "$XRAY_CONFIG_PATH"
    schedule_restart
    CHANGED="true"
else
    CHANGED="false"
    BACKUP_PATH=""
fi

if [[ -z "$NAME" ]]; then
    NAME="$EMAIL"
fi

LINK="$("$BUILD_LINK_SCRIPT" --uuid "$UUID" --name "$NAME" --json | jq -r '.link')"

if [[ "$AS_JSON" == "1" ]]; then
    jq -n \
        --arg status "ok" \
        --arg uuid "$UUID" \
        --arg previous_email "$CURRENT_EMAIL" \
        --arg email "$EMAIL" \
        --arg name "$NAME" \
        --arg link "$LINK" \
        --arg changed "$CHANGED" \
        --arg inbound_tag "$XRAY_INBOUND_TAG" \
        --arg config_path "$XRAY_CONFIG_PATH" \
        --arg backup_path "$BACKUP_PATH" \
        '{
            status: $status,
            uuid: $uuid,
            previous_email: $previous_email,
            email: $email,
            name: $name,
            link: $link,
            changed: ($changed == "true"),
            inbound_tag: $inbound_tag,
            config_path: $config_path,
            backup_path: $backup_path
        }'
else
    printf '%s\t%s\t%s\n' "$UUID" "$CURRENT_EMAIL" "$EMAIL"
fi
