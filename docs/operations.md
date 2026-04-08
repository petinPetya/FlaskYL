# Operations

## Current VPN Baseline

- VPN server: `147.45.224.143`
- Xray config: `/usr/local/etc/xray/config.json`
- Xray env: `/etc/lowlands-vpn/xray.env`
- Helper scripts:
  - `/usr/local/sbin/xray-add-client`
  - `/usr/local/sbin/xray-remove-client`
  - `/usr/local/sbin/xray-build-vless-link`
  - `/usr/local/sbin/xray-list-clients`
- Live inbound tag: `vless-reality-in`
- Xray API: `127.0.0.1:10085`

## Export A Baseline Snapshot

Use the local export script from the Flask machine:

```bash
./scripts/export-vpn-server-state.sh \
  --host 147.45.224.143 \
  --user root \
  --key-path /home/senamorsin/.ssh/lowlands_vpn_xray
```

The script creates:

- a timestamped directory under `backups/vpn-server-state-*`
- a `.tar.gz` archive
- a matching `.sha256` checksum

It exports:

- live `config.json`
- live `xray.env`
- IPv4/IPv6 sysctl state
- routes and listening sockets
- `systemctl status xray`
- recent `journalctl -u xray`
- `xray version`
- live Xray client list

Private SSH keys are intentionally not copied into this archive. Keep them in a
separate secret store.

## Restricted SSH User

The goal is to stop giving the Flask app full `root` SSH access. The restricted
account can only execute the Xray helper scripts through a forced command
dispatcher, and only through a narrow `sudoers` allowlist for those scripts.

Install it from the Flask machine:

```bash
./scripts/setup-restricted-vpn-ssh.sh \
  --host 147.45.224.143 \
  --root-key-path /home/senamorsin/.ssh/lowlands_vpn_xray \
  --app-public-key-path /home/senamorsin/.ssh/lowlands_vpn_xray.pub
```

Defaults:

- restricted user: `lowlands-vpn`
- dispatcher path: `/usr/local/sbin/xray-ssh-dispatch`
- helper paths remain unchanged

After installation, switch Flask to:

```bash
export VPN_SSH_USER='lowlands-vpn'
```

## Verify Restricted Access

List clients:

```bash
ssh -F /dev/null -i /home/senamorsin/.ssh/lowlands_vpn_xray \
  lowlands-vpn@147.45.224.143 \
  /usr/local/sbin/xray-list-clients --json
```

Add a disposable client:

```bash
ssh -F /dev/null -i /home/senamorsin/.ssh/lowlands_vpn_xray \
  lowlands-vpn@147.45.224.143 \
  /usr/local/sbin/xray-add-client --email smoke-test@xray --uuid 00000000-0000-0000-0000-000000000099 --name smoke-test
```

Remove it:

```bash
ssh -F /dev/null -i /home/senamorsin/.ssh/lowlands_vpn_xray \
  lowlands-vpn@147.45.224.143 \
  /usr/local/sbin/xray-remove-client --uuid 00000000-0000-0000-0000-000000000099
```

Anything outside the allowed helper commands must be rejected.

## Restore Checklist

1. Provision Ubuntu.
2. Install Xray and restore `/usr/local/etc/xray/config.json`.
3. Restore `/etc/lowlands-vpn/xray.env`.
4. Restore helper scripts under `/usr/local/sbin`.
5. Restore sysctl overrides if present.
6. Verify `systemctl status xray`.
7. Verify `xray-list-clients --json`.
8. Start Flask with the correct `.flask-env`.
