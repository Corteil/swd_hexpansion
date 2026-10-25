# swd_hexpansion
app and hardware design for a SWD programmer/debug probe for Raspberry Pi RP2040 and RP235x projects

## Concept

An EMF badge (Tildagon-style, ESP32-S3 running MicroPython) acts as the SWD
programmer for an RP2350 hexpansion that has no USB-C. A second hexpansion slot
with a proto hexpansion drives SWCLK/SWDIO (direct GPIO pins) to a debug header
on the target hex, plus a GND wire.

## Files

- `swd_rp2350.py` - MicroPython SWD bit-bang sketch (multidrop, ADIv6) for the badge side.
- `flash_rp2350.py` - flash programming through the RP2350 bootrom (see below); `uf2.py` - .uf2/.bin reader.
- `targets.py` - target profiles (RP2350, RP2040, generic Cortex-M); `leds.py` - activity LEDs.
- `app.py`, `ui.py`, `tile.py`, `tiles.py`, `filepicker.py` - the Tildagon app (GUI scaffold).
- `tildagon.toml`, `metadata.json` - app store metadata.

## GUI

Seven tiles in a carousel, LEFT/RIGHT scroll and wrap round: Connect, Flash,
Files, Memory, Core, Serial, Settings. CONFIRM opens a tile, CANCEL goes back.
A USB keyboard works like the buttons (arrows, Enter, Escape), types into text
fields, and a letter jumps to the next file in the file explorer. Flash's
"Choose file" opens Files as a picker and returns when a `.uf2`/`.bin` is chosen.

The GUI is a scaffold: the SWD calls behind the tiles are not wired in yet, and
Flash runs a labelled dry run so the progress ring and bar can be seen.

## Status

Untested. Unverified items: TARGETSEL values, whether the dormant-state wake is
needed, MEM-AP register offsets and CSW bits. Verify against OpenOCD `-d3` traces
from a Raspberry Pi Debug Probe on a known-good board. Flash routines are not written yet.

## Flashing (RP2350)

`flash_rp2350.Flasher.run(path)` is a generator yielding `(stage, fraction)` (0 erase, 1 program,
2 verify, 3 done). It resets the chip with the core caught at its first instruction, does what
OpenOCD's rp2350 driver does before calling the bootrom (reset ACCESSCTRL, initialise the RCP,
clear the bootrom state), then runs the bootrom's `connect_internal_flash`, `flash_exit_xip`,
`flash_range_erase` and `flash_range_program` on the halted core, one 4 KB sector at a time from
a RAM buffer, and verifies by reading back over XIP. The sequence and the RCP init code come
from a logic-analyser capture of OpenOCD. Checked on a Pico 2: images of 39 KB and 70 KB (one
with two separate regions) flashed and matched an independent OpenOCD dump of the flash.
Speed: about 7 KB/s end to end (program about 17 KB/s, verify about 20 KB/s).

`flash_range_erase` and `flash_range_program` return nothing, so there is no status to check; a
failure shows as a timeout or a verify mismatch.

**Erase all** (third row of the Flash tile, needs a second OK to confirm) erases the whole flash so the
chip is as new: all `0xFF`, which makes the bootrom fall back to USB boot mode. It then spot-checks the
first and last 256 bytes of every 64 KB block and restarts the target. The flash size comes from the
target profile (4 MB for the RP2350 entry), so a board with more flash is only erased up to that size.
Checked on a Pico 2: a full dump of the 4 MB with OpenOCD afterwards was all `FF`, and the chip appeared
as `2e8a:000f RP2350 Boot` on USB.
