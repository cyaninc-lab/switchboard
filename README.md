# Release signatures

The signature (`.sig`) of every Switchboard download, one folder per version (`v0.1.14/…`).
They're kept here rather than on the releases page so it only lists the downloads.

Installed apps don't use these files: each release's `latest.json` carries the same signatures, and the app
checks every update against the public key built into it before installing. They're here for checking a
download by hand, e.g. with [minisign](https://jedisct1.github.io/minisign/) and the public key in
[`desktop/src-tauri/tauri.conf.json`](https://github.com/cyaninc-lab/switchboard/blob/main/desktop/src-tauri/tauri.conf.json)
(`plugins.updater.pubkey`, base64-encoded).
