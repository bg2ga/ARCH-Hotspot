# Deployment provisioning

## 1. Tunnel private key

The public repository intentionally has no `arch-tunnel.key`.

For your own deployment:

1. Generate a dedicated SSH key on your build host.
2. Put the private key at `etc/mmdvm/arch-tunnel.key` with mode `0600` only inside your private build workspace.
3. Install the matching public key on the remote tunnel account.
4. Never commit or publish the private key.

`install.sh` only installs this file when it exists in the package.

## 2. Remote debug secret

`install.sh` creates `/etc/mmdvm/remote-debug.secret` with 32 random bytes when absent. Keep it local to the device and do not publish it.

To rotate the secret on a live device, delete `/etc/mmdvm/remote-debug.secret` and rerun `install.sh`, or generate a new 64-character hex value and restart `arch-remote-agent.service` and `arch-remote-tunnel.service`.

## 3. Verify

```bash
sudo /usr/local/bin/arch-remote-debug-ctl status
sudo systemctl status arch-remote-agent.service arch-remote-tunnel.service
```

The status output must show both a present secret and active services after provisioning.