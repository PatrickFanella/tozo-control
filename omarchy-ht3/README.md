# TOZO HT3 for Omarchy

A native Omarchy Shell / Quickshell top-bar widget with a headphone popup:

- HT3 playback volume and mute through PipeWire.
- Normal, ANC and transparency modes, with device readback.
- Ten-band hardware EQ, -5 to +5 dB, with Apply, Flat and Revert edits.
- Battery status, disconnect/error states, keyboard-accessible controls.

Requires Omarchy 4.x with Quickshell, BlueZ, PipeWire and `/usr/bin/python3`.
No pip packages, GTK window or separate system service is required. Pair the
HT3 using normal Bluetooth settings before opening the plugin.

From this repository:

```bash
mise run test
mise run install
```

`install` validates and copies the plugin to
`~/.config/omarchy/plugins/patrickfanella.ht3`, backs up any existing copy and
shell configuration, and enables it in the right section of the current bar.
It restarts Omarchy Shell to clear Qt's component cache. The existing bar remains
selected. Installed source is a copy; run install again
after changing source. It is not a git-managed marketplace installation.

Click the headphone icon to open. One paired HT3 connects its controls
automatically; multiple paired HT3s can be selected. Closing the popup releases
the RFCOMM control connection without disconnecting Bluetooth audio. A per-device
lock prevents two monitor popups from issuing competing commands.

The volume slider only affects the selected HT3 output, even when another device
is the default. EQ sliders are drafts until Apply; Flat is also a draft. Closing
the popup discards unapplied edits. Noise mode buttons apply immediately and
highlight only after readback. Spatial audio must be disabled in the TOZO app
before using the hardware EQ.

If the phone app is holding the control channel, close it and reopen the popup.
If Bluetooth audio is disconnected, connect it using Omarchy's Bluetooth menu.

Disable with `omarchy plugin disable patrickfanella.ht3`.
See [PROTOCOL.md](PROTOCOL.md) for transport evidence and provenance.
