# Security Policy

## Never commit

- `etc/mmdvm/arch-tunnel.key`
- `etc/mmdvm/remote-debug.secret`
- device IDs, host keys, private certificates or account passwords
- captured logs containing credentials or personal data

## Tunnel key provisioning

The repository deliberately does not ship the shared tunnel private key. Each deployment must provide its own key in `etc/mmdvm/arch-tunnel.key` before generating a production package and must authorize the matching public key on the remote tunnel server.

## Remote debug secret

`install.sh` creates `/etc/mmdvm/remote-debug.secret` with per-device random data and mode `0600` when the file is absent. The agent, tunnel and watchdog read this file at runtime.