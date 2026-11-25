# Corresponding source

This repository tracks the sanitized source tree corresponding to ARCH Hotspot public channel version 1.0.170.

It contains the ARCH installation scripts, Web UI, service definitions, gateway configuration and related integration code. Third-party source and build inputs are obtained by `build_upstream.sh` from the configured source bundle.

Security modifications from the distributed package:

- the shared tunnel private key is excluded;
- the former fixed remote-debug secret is replaced by a per-device random secret generated at install time.

If you distribute a modified binary, publish the matching source revision and preserve all upstream license notices.