#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  setup-authoritative-dns-ubuntu.sh --host <server_ip> --root-key-path <path>
                                    [--zone <domain.zone>] [--web-ip <ipv4>]
                                    [--admin-mail-host <host.label>]
                                    [--ns1-label <label>] [--ns2-label <label>]
                                    [--root-user <user>] [--port <port>]
                                    [--ssh-config-file <path>]
                                    [--skip-ufw]

Sets up an authoritative DNS service (BIND9) on an Ubuntu server.

What it does:
1) installs bind9 and bind9-utils,
2) configures named for authoritative mode (recursion disabled),
3) opens TCP/UDP 53 in UFW (unless --skip-ufw),
4) if --zone is provided, creates/updates a DNS zone file with:
   - NS records (ns1/ns2),
   - A records for @, www, ns1, ns2,
   - MX + SPF baseline records.
EOF
}

fail() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

HOST=""
ROOT_USER="root"
ROOT_KEY_PATH=""
PORT="22"
SSH_CONFIG_FILE="/dev/null"

ZONE=""
WEB_IP=""
ADMIN_MAIL_HOST="admin"
NS1_LABEL="ns1"
NS2_LABEL="ns2"
SKIP_UFW="false"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --host)
            HOST="${2:-}"
            shift 2
            ;;
        --root-user)
            ROOT_USER="${2:-}"
            shift 2
            ;;
        --root-key-path)
            ROOT_KEY_PATH="${2:-}"
            shift 2
            ;;
        --port)
            PORT="${2:-}"
            shift 2
            ;;
        --ssh-config-file)
            SSH_CONFIG_FILE="${2:-}"
            shift 2
            ;;
        --zone)
            ZONE="${2:-}"
            shift 2
            ;;
        --web-ip)
            WEB_IP="${2:-}"
            shift 2
            ;;
        --admin-mail-host)
            ADMIN_MAIL_HOST="${2:-}"
            shift 2
            ;;
        --ns1-label)
            NS1_LABEL="${2:-}"
            shift 2
            ;;
        --ns2-label)
            NS2_LABEL="${2:-}"
            shift 2
            ;;
        --skip-ufw)
            SKIP_UFW="true"
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

[[ -n "$HOST" ]] || fail "Missing --host"
[[ -n "$ROOT_KEY_PATH" ]] || fail "Missing --root-key-path"
[[ -f "$ROOT_KEY_PATH" ]] || fail "SSH key not found: $ROOT_KEY_PATH"

if [[ -n "$ZONE" ]] && ! [[ "$ZONE" =~ ^[a-z0-9.-]+$ ]]; then
    fail "--zone must contain only lowercase letters, digits, dots and hyphens"
fi

if [[ -n "$WEB_IP" ]] && ! [[ "$WEB_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
    fail "--web-ip must be a valid IPv4 format"
fi

require_command ssh

ZONE_ARG="${ZONE:-__EMPTY__}"
WEB_IP_ARG="${WEB_IP:-__EMPTY__}"

ssh_base=(
    ssh
    -F "$SSH_CONFIG_FILE"
    -i "$ROOT_KEY_PATH"
    -p "$PORT"
    -o BatchMode=yes
    -o ConnectTimeout=10
    "${ROOT_USER}@${HOST}"
)

printf 'Configuring DNS on %s...\n' "$HOST"
"${ssh_base[@]}" "bash -s" -- \
    "$ZONE_ARG" \
    "$WEB_IP_ARG" \
    "$ADMIN_MAIL_HOST" \
    "$NS1_LABEL" \
    "$NS2_LABEL" \
    "$SKIP_UFW" <<'EOF'
set -euo pipefail

ZONE="$1"
WEB_IP="$2"
ADMIN_MAIL_HOST="$3"
NS1_LABEL="$4"
NS2_LABEL="$5"
SKIP_UFW="$6"

if [[ "$ZONE" == "__EMPTY__" ]]; then
    ZONE=""
fi
if [[ "$WEB_IP" == "__EMPTY__" ]]; then
    WEB_IP=""
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends bind9 bind9-utils dnsutils

if [[ -z "$WEB_IP" ]]; then
    WEB_IP="$(ip -4 route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')"
fi
[[ -n "$WEB_IP" ]] || {
    echo "Unable to detect WEB_IP automatically." >&2
    exit 1
}

OPTIONS_FILE="/etc/bind/named.conf.options"
cp -a "$OPTIONS_FILE" "${OPTIONS_FILE}.bak.$(date +%Y%m%d%H%M%S)"
cat > "$OPTIONS_FILE" <<EOCFG
options {
    directory "/var/cache/bind";
    recursion no;
    allow-query { any; };
    allow-transfer { none; };
    listen-on port 53 { 127.0.0.1; ${WEB_IP}; };
    listen-on-v6 { none; };
    dnssec-validation auto;
    auth-nxdomain no;
    version "not disclosed";
};
EOCFG

if [[ -n "$ZONE" ]]; then
    LOCAL_CONF="/etc/bind/named.conf.local"
    ZONE_FILE="/etc/bind/db.${ZONE}"
    ZONE_BEGIN="# LOWLANDS_ZONE_BEGIN ${ZONE}"
    ZONE_END="# LOWLANDS_ZONE_END ${ZONE}"

    if grep -Fq "$ZONE_BEGIN" "$LOCAL_CONF"; then
        sed -i "/^${ZONE_BEGIN//\//\\/}$/,/^${ZONE_END//\//\\/}$/d" "$LOCAL_CONF"
    fi

    cat >> "$LOCAL_CONF" <<EOZONECONF

${ZONE_BEGIN}
zone "${ZONE}" {
    type master;
    file "${ZONE_FILE}";
    allow-update { none; };
};
${ZONE_END}
EOZONECONF

    serial="$(date +%Y%m%d%H)"
    cat > "$ZONE_FILE" <<EOZONE
\$TTL 3600
@   IN  SOA ${NS1_LABEL}.${ZONE}. ${ADMIN_MAIL_HOST}.${ZONE}. (
        ${serial} ; serial
        3600      ; refresh
        1800      ; retry
        1209600   ; expire
        300       ; minimum
)

@               IN NS     ${NS1_LABEL}.${ZONE}.
@               IN NS     ${NS2_LABEL}.${ZONE}.

@               IN A      ${WEB_IP}
www             IN A      ${WEB_IP}
${NS1_LABEL}    IN A      ${WEB_IP}
${NS2_LABEL}    IN A      ${WEB_IP}
mail            IN A      ${WEB_IP}

@               IN MX 10  mail.${ZONE}.
@               IN TXT    "v=spf1 a mx ~all"
_dmarc          IN TXT    "v=DMARC1; p=none; rua=mailto:${ADMIN_MAIL_HOST}@${ZONE}"
EOZONE

    named-checkzone "$ZONE" "$ZONE_FILE"
fi

named-checkconf
systemctl enable --now named
systemctl restart named
systemctl is-active named >/dev/null

if [[ "$SKIP_UFW" != "true" ]] && command -v ufw >/dev/null 2>&1; then
    if ufw status | grep -q "Status: active"; then
        ufw allow 53/tcp
        ufw allow 53/udp
    fi
fi

echo "DNS_SETUP_OK"
echo "WEB_IP=${WEB_IP}"
if [[ -n "$ZONE" ]]; then
    echo "ZONE=${ZONE}"
    dig @127.0.0.1 "${ZONE}" A +short || true
fi
EOF

printf '\nDone.\n'
printf 'Host: %s\n' "$HOST"
if [[ -n "$ZONE" ]]; then
    printf 'Zone: %s\n' "$ZONE"
fi
