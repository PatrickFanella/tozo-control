# HT3 protocol evidence

This plugin is an independently written Python/Quickshell implementation.
The original tozo-control repository is the starting concept, but its T10 BLE
transport and EQ encoding do not match the tested HT3.

Protocol facts were located in
[cheesyserg/Tozo-Kontrol](https://github.com/cheesyserg/Tozo-Kontrol/blob/4bb48b76e1782a3a0437312825a95ac45d7b53a3/tozo_kontrol.cpp)
at revision `4bb48b76e1782a3a0437312825a95ac45d7b53a3`. That checkout has no
license file; its application source and assets are not incorporated here.

Live reads on a paired TOZO HT3 on 2026-09-19 confirmed:

- Bluetooth Classic RFCOMM, channel 1. Closing this socket leaves the audio
  profile connected. Do not use Bleak connect/disconnect on the Classic address.
- Frame: opcode, parameter, payload length, payload, sum(payload) modulo 256.
- GET: opcode 0 with empty payload. SET: opcode 16.
- Battery GET `02`: one byte, 0–100. Captured response: `0002015959` (89%).
- Noise mode GET `30`: 0 normal, 1 ANC, 2 transparency (other recognized values:
  3 wind, 4 leisure, 6 adaptive). Captured response: `0030010202`.
- Normal SET `04` payload `00`; ANC SET `04` payload `01`;
  transparency SET `05` payload `01`. Plugin does not alter the saved boot mode.
- Spatial GET `13`: boolean. Captured response: `0013010000`.
- EQ GET `0b`: ten signed gain bytes followed by ten frequency codes.
  Gains use tenths of a dB, range -50 to 50. Frequency codes are
  `05 06 07 08 0a 0c 0e 10 12 14` (20, 50, 100, 200, 400, 800, 1600,
  3200, 6400, 12800 Hz). SET includes those 20 bytes plus commit byte `01`.

RFCOMM is a byte stream: reads may split packets or combine several packets.
Unsolicited vendor data with a different envelope also occurs. The parser
accepts only known GET reply lengths and checksums; SET acknowledgements are
not treated as confirmed state. Every mutation is followed by a GET readback.
EQ is disabled while spatial audio is active or the EQ profile is unrecognized.

Volume uses the exact paired HT3 address to select its PipeWire output node.
It does not change the default output or fall back to unrelated speakers.
The plugin caps playback volume at 100%.

## Local qualification

On 2026-09-19 the new helper changed this HT3 to ANC, Normal and Transparency,
and confirmed each through GET `30`. It changed the 20 Hz EQ gain by -0.1 dB,
read back the complete curve, then restored the original curve and Transparency
mode with matching readbacks. The live Omarchy popup displayed 89% battery,
75% playback volume and the same curve. QML loaded in Omarchy 4.0.4-1 and the
existing custom bar remained present once per monitor on three monitors.

Automated checks cover captured stream fragmentation, vendor packet noise,
checksum rejection, SET-ACK rejection, signed EQ encoding, invalid inputs,
spatial-audio gating, stale capability clearing and mutation readback failure.
Physical acoustic measurements, reboot persistence and other HT3 firmware
versions have not been tested.
