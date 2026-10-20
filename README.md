# swd_hexpansion
app and hardware design for a SWD programmer/debug probe for Raspberry Pi RP2040 and RP235x projects

## Concept

An EMF badge (Tildagon-style, ESP32-S3 running MicroPython) acts as the SWD
programmer for an RP2350 hexpansion that has no USB-C. A second hexpansion slot
with a proto hexpansion drives SWCLK/SWDIO (direct GPIO pins) to a debug header
on the target hex, plus a GND wire.

## Files

- `swd_rp2350.py` - MicroPython SWD bit-bang sketch (multidrop, ADIv6) for the badge side.

## Status

Untested. Unverified items: TARGETSEL values, whether the dormant-state wake is
needed, MEM-AP register offsets and CSW bits. Verify against OpenOCD `-d3` traces
from a Raspberry Pi Debug Probe on a known-good board. Flash routines are not written yet.
