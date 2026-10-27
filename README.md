# SWD Programmer

A Tildagon (EMF Camp 2024 badge) app that turns the badge into an SWD programmer and debugger
for Raspberry Pi **RP2040** and **RP2350** boards. It flashes `.uf2` and `.bin` files, reads and
halts the target, and has a UART terminal that can record to the SD card.

It exists for hexpansions that carry an RP2350 but have no USB-C connector: plug the target's
debug pads into a proto hexpansion in another slot and program it from the badge.

**User guide:** [docs/USER_GUIDE.md](docs/USER_GUIDE.md)

## What it does

| Tile | What you can do |
|---|---|
| **Connect** | Wake the target and read its debug ID, pick the chip type (or auto-detect), read the flash chip's maker and size. |
| **Flash** | Check a `.uf2` or `.bin` suits the chip, then erase, program and verify it, with a progress ring. Erase the whole flash. |
| **Files** | Browse the badge's storage and the SD card, and choose the file to flash (with a confirm step). |
| **Memory** | Read memory a screen at a time, jump to any address. |
| **Core** | Halt, resume, single-step and reset the target's CPU, with PC and SP shown. |
| **Serial** | UART terminal to the target. Save a snapshot or record everything to a file, optionally with a time on each line. |
| **Settings** | SWD speed, slot, rescue reset, a wiring check (Pin check) and slot power cycle. |

## Hardware

- A badge (ESP32-S3, MicroPython) running the standard Tildagon firmware, which provides the `system.hexpansion` modules the app uses.
- A **proto hexpansion** in one slot (slot B by default). SWD is bit-banged from its four
  high-speed pins; three LEDs show activity.
- The target board, powered **separately** (for example by its own USB cable). 

Wiring, for the slot the proto hexpansion is in (HS_F to HS_I are the slot's four high-speed pins):

| Proto pad | Goes to | Notes |
|---|---|---|
| HS_F | target SWCLK | |
| HS_G | target SWDIO | |
| GND | target GND | needed |
| HS_H | target UART RX | the badge transmits here (optional) |
| HS_I | target UART TX | the badge receives here (optional) |
| LS_A | green LED | lights on UART transmit |
| LS_B | blue LED | lights on UART receive |
| LS_C | white LED | lights on SWD activity |

The LEDs are wired from 3V3 to the pin, so they light when the pin is driven low. There is no
reset line: the app resets the target over SWD, and recovers a hung one with a rescue reset.

The Serial tile has a row that swaps the UART pair, so a wrong guess about TX and RX is a setting,
not a rewire. **Settings → Pin check** reports whether SWD answers on the expected pins, on swapped
pins or on none, which pin carries the target's UART output, and lights the three LEDs in turn.

## Tested boards

| Board | Chip | Flash (read from the chip) |
|---|---|---|
| Raspberry Pi Pico | RP2040 | 2 MB (Winbond `EF4015`) |
| Pimoroni Tiny2040 | RP2040 | 8 MB (Winbond `EF4017`) |
| Raspberry Pi Pico 2 | RP2350 | 4 MB (Winbond `EF4016`) |
| Metro RP2350 | RP2350 | 16 MB (Winbond `EF4018`) |
| Pimoroni Pico LiPo 2 XL W | RP2350B | 16 MB (Winbond `EF4018`) |

On the Pico, Tiny2040, Pico 2 and Metro, flash was written and verified and Erase all was run. Images
placed deeper in the flash were also written and read back: at 2 MB and in the last sector on the
Pico 2, at 4 MB and in the last sector on the Tiny2040, and at 4 MB, 8 MB and in the last sector on
the Metro. On every one of the four, an image past the end of the flash was refused. The LiPo was
connected, identified and its flash size read; I have not written to it.

Only the Arm (Cortex-M) side is supported: core 1 and the RP2350's RISC-V cores are not reachable.

## Install

Copy the app files to the badge, for example with `mpremote`:

```sh
mpremote fs mkdir :apps/swd_programmer
for f in __init__.py app.py ui.py tile.py tiles.py filepicker.py leds.py pincheck.py \
         swd_rp2350.py flash_rp2350.py flash_rp2040.py targets.py uf2.py \
         metadata.json tildagon.toml; do
  mpremote fs cp $f :apps/swd_programmer/$f
done
```

then reset the badge and open **SWD Programmer** from the menu. The app also works from the app
store layout (`tildagon.toml`, `metadata.json`).

Put `.uf2` and `.bin` files on the SD card (or the badge's flash) and choose them from the Files tile.
On the badge this was developed on, the SD card appears only after a hard reset.

## Repository layout

| File | Purpose |
|---|---|
| `app.py` | The app: carousel, navigation, text entry, pop-ups |
| `ui.py` | Drawing helpers for the round display (icons, progress ring, text that fits the glass) |
| `tile.py` | Shared tile classes, the link to the target, the flasher lookup |
| `tiles.py` | The Connect, Flash, Memory, Core, Serial and Settings tiles |
| `filepicker.py` | The Files tile and the file browser |
| `pincheck.py` | The Pin check |
| `swd_rp2350.py` | SWD driver: bit-bang through the ESP32-S3 GPIO registers, debug port and memory access, core control |
| `flash_rp2350.py` | RP2350 flashing, and the parts shared with the RP2040 (size detection, stages, verify) |
| `flash_rp2040.py` | RP2040 flashing |
| `targets.py` | Target profiles (RP2040, RP2350, generic Cortex-M) |
| `uf2.py` | `.uf2` and `.bin` reading and checking |
| `leds.py` | The three activity LEDs |
| `tools/` | `swd_orientation.py` (find mis-wired SWD pins), `capture_swd.sh` and `swd_bits.py` (decode a logic-analyser capture) |
| `test_firmware/` | A small Pico SDK program (blinks the LED, prints ticks, echoes UART input) used to test the app |

## How it works

**SWD.** The driver bit-bangs SWD with a `viper` function that writes straight to the ESP32-S3 GPIO
registers: about 450 kHz at the `max` setting, with block reads around 33 KB/s. A line reset and the
dormant wake-up bring the chip up. The RP2350 answers as a single-drop target (ADIv6 with the core
at AP `0x2000`). The RP2040 is multidrop (ADIv5): core 0 is selected with TARGETSEL `0x01002927`
after a dormant wake-up.

**Flashing** calls the chip's own bootrom functions on the halted core, the way the Pico SDK does:
`connect_internal_flash`, `flash_exit_xip`, `flash_range_erase`, `flash_range_program`, then
`flush_cache` and `enter_cmd_xip`. Each 4 KB sector goes through a RAM buffer, and the result is
verified by reading the flash back over XIP. On the RP2350 the sequence needs the core caught at its
first instruction and a short Thumb stub that enables the redundancy coprocessor; that sequence came
from a logic-analyser capture of OpenOCD. The bootrom erase and program functions return nothing,
so a failure shows as a timeout or a verify mismatch.

The flash size is read from the chip's JEDEC ID (command `0x9F`, through the RP2040's SSI or the
RP2350's QMI), capped at 16 MB. Flash, Erase all and the size check all use it.

**Every file is vetted for the connected chip before anything is erased** (`Flasher.check`, also shown
on the Files tile's confirm screen). It must be a sound, non-empty file; a UF2's family must be the
chip's (RP2040; or RP2350 Arm-S, Arm-NS or RISC-V; the `data` family suits both); it must be aimed at
flash and fit in it; and an image starting at the beginning of flash must carry what that chip's
bootrom needs to run it. On the RP2040 that is a second-stage boot loader whose last four bytes are a
CRC-32 (polynomial `0x04C11DB7`, seed `0xFFFFFFFF`, no bit reversal) of the first 252; on the RP2350 it is
an `IMAGE_DEF` block, found by its opening word `0xFFFFDED3` in the first 4 KB. Without these an image
would flash and verify but never boot. Both checks were validated against real images (an SDK build and a
MicroPython image for the RP2040, an SDK build and the Metro's HDMI image for the RP2350) before being
enforced. The final size check runs after the chip's flash size has been read.

Measured speed, end to end including verify: about 10 to 15 KB/s. Erasing the whole flash takes
3 s for 4 MB, 29 s for 8 MB and 68 s for 16 MB.

**Reset without a reset line** is done over SWD (`SYSRESETREQ`, or catching the core at its first
instruction). **Rescue** recovers a chip the debug port can still reach: on the RP2350 it pulses
`RESCUE_RESTART` in the RP_AP, which resets everything but the debug port and leaves both cores in
the bootrom. On the RP2040 it uses the separate rescue debug port.

## Things to know

- **RP2040 and the rescue port.** On an awake RP2040, a plain line reset with no TARGETSEL selects the
  rescue port, and the core ports then stop answering until a rescue reset or a power cycle. The app
  tries the RP2040 core select first, and refuses to power up the rescue port.
- **Flash reads from a chip in the bootrom return zeros or junk** (a blank board, BOOTSEL, after a
  rescue), because XIP is off. The Memory tile notices this and turns XIP on, which leaves the core
  halted.
- **Slot power** cuts the slot's power by driving its hexpansion-detect pin, which is how the badge
  firmware's own Power Off screen does it. The badge's hexpansion manager sees that as a removal and a
  fresh insertion. The app refuses slots that have an app or mounted storage. A target powered over
  its own USB cable is not reset by it.
- **The badge's clock is unset** (it counts from the year 2000) until it syncs over WiFi, so Serial
  time stamps fall back to seconds since the terminal opened.
- **A very long halt can upset a target's USB.** Halting a Pico for a long time makes the host give up
  enumerating it; unplug and replug the Pico.

## Development notes

- Test firmware: `test_firmware/` builds with Pico SDK 2.3.0 (`cmake -G Ninja ..`; add
  `-DPICO_BOARD=pico` for an RP2040). It prints `tick N` every 500 ms on UART0 (GP0 TX, GP1 RX,
  115200) and blinks the LED.
- `mpremote run` and `exec` soft-reset the badge, which unmounts the SD card; `mpremote reset` followed
  by a wait of about 30 s brings it back. `mpremote resume ...` keeps already-imported modules, so after
  editing a file delete the `swd_programmer` entries from `sys.modules` before re-importing it.
- Connecting with `mpremote` interrupts the running app; reset the badge afterwards.
- `tools/capture_swd.sh` records SWD with a Kingst LA2016 on channels 0 and 1 and decodes the bit
  stream with `swd_bits.py`; comparing a capture with an OpenOCD run on a Raspberry Pi Debug Probe was
  how the RP2350 sequence was verified.

## Licence

MIT, see [LICENSE](LICENSE).
