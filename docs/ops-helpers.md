# Ops helpers

`scripts/` holds host-side helpers. They are **not** part of the shipped image
or the MCP: they run on an operator's own
[Sunshine](https://github.com/LizardByte/Sunshine) streaming host.

**`sunshine-sync-steam.ps1`** rebuilds Sunshine's app list from the host's
locally installed Steam games (`appmanifest_*.acf`), so a Moonlight client shows
one launchable tile per game with box art rather than only "Desktop". It talks
to the local Sunshine web API, which runs as LocalSystem and writes the
admin-owned config, so it needs no Steam Web API key and no elevation. The
Sunshine password resolves from `SUNSHINE_WEB_PASSWORD` then a prompt and never
enters the repo. It is idempotent, and `-DryRun` previews.

**`steam-airgap.ps1`** toggles a Windows Firewall block on Steam's networking
executables with `-On`, `-Off`, and `-Status`, forcing the client offline while
Tailscale, Sunshine, Moonlight, and the game's own traffic stay up. That lets an
airgapped streaming host keep running a game while the same account plays online
elsewhere, because Steam cannot be kicked if it cannot reach its servers. It
self-elevates for the firewall writes.

**`display-mode/`** flips the host between **native** mode (physical monitor
only) and **remote** mode (the Virtual Display Driver up for Sunshine).
`Set-DisplayMode.ps1 -Mode native|remote|toggle|status` drives it: disabling the
VDD writes `CONFIGFLAG_DISABLED`, which persists across reboots, so native
survives a restart with no autostart helper. Leaving remote mode also restores
the monitor's best 32bpp mode via `ChangeDisplaySettingsEx` +
`CDS_UPDATEREGISTRY`, because tearing the VDD down otherwise parks the physical
panel at whatever fallback the stream negotiated. `status` is read-only and
needs no elevation, and the state-changing modes self-elevate through UAC.
Sunshine drives `mode-remote.cmd` and `mode-native.cmd` from `global_prep_cmd`
(do and undo), and a desktop shortcut drives `toggle-display-mode.cmd` by hand.
`Install-DisplayMode.ps1` is the deploy step: run it elevated from the checkout
and it copies the scripts to `C:\ProgramData\DisplayMode` (the fixed path
Sunshine and the shortcut point at), rewires `global_prep_cmd`, and lands the
host in native mode. The repo is the source, so never edit the deployed copy.

## See also

- [FEATURES.md](FEATURES.md) - the capability inventory.
