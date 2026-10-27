# SWD Programmer: user guide

This guide covers wiring a board to the badge, connecting, flashing, looking inside the chip,
using the serial terminal, and what to do when something goes wrong. For how the app works,
see the [README](../README.md).

Contents: [Before you start](#before-you-start) · [Controls](#controls) · [First connection](#first-connection) ·
[The tiles](#the-tiles) · [Common jobs](#common-jobs) · [Troubleshooting](#troubleshooting) · [Limits](#limits)

---

## Before you start

You need:

- the badge, with a **proto hexpansion** fitted (slot B unless you change it in Settings);
- the **target board**: an RP2040 board (Pico, Tiny2040, ...) or an RP2350 board (Pico 2, Metro RP2350, ...);
- three wires between the target's SWD pads and the proto hexpansion; two more if you want the serial terminal;
- a separate power source for the target, usually its own USB cable. **The badge does not power the target.**
  Plug the target in before you connect.

### Wiring

| Proto pad | Target | Needed for |
|---|---|---|
| **HS_F** | SWCLK | everything |
| **HS_G** | SWDIO | everything |
| **GND** | GND | everything |
| **HS_H** | UART RX | serial terminal (the badge sends here) |
| **HS_I** | UART TX | serial terminal (the badge listens here) |

On a Raspberry Pi Pico or Pico 2 the debug header is three pins in the order SWCLK, GND, SWDIO, so
GND is the middle one. Other makers can differ (Pimoroni documents its original Pico LiPo as wired the
other way round), so check the board's pinout, or run **Settings → Pin check** if nothing answers.

The proto hexpansion's three LEDs mean: **green** = UART transmit, **blue** = UART receive,
**white** = SWD activity.

If you put the proto hexpansion in another slot, set the slot number in **Settings → Slot**.

---

## Controls

The badge's six edge buttons are called **UP, DOWN, LEFT, RIGHT, OK** (confirm) and **CANCEL**.

- On the **carousel** (the screen with the big icon), LEFT and RIGHT move between the seven tiles and wrap
  round. OK opens a tile. CANCEL sends the app to the background.
- **Inside a tile**, UP and DOWN move between rows, OK acts on the highlighted row, and LEFT and RIGHT change
  rows that show `< ... >`. CANCEL goes back one level and, on the top level of a tile, returns to the carousel.
- A **USB keyboard** works like the buttons (arrow keys, Enter, Escape), types into text boxes, and in the
  file browser a letter jumps to the next file starting with it.

The **top line** of every screen shows the target and its state: `no link`, `RP2350 running`,
`RP2040 halted` and so on. Short messages appear as a pop-up at the bottom for a couple of seconds.

Some actions that change or erase things need **a second OK** to confirm: the row changes to read
`OK to confirm ...` first.

---

## First connection

1. Power the target and make sure the wires are in place.
2. Open **SWD Programmer** from the badge menu.
3. Scroll to the **Connect** tile, press OK, then OK on the first row (`Connect`).
4. The pop-up says `Connected: RP2350` (or RP2040), the first row becomes `Disconnect`, and the top line
   shows the chip and `running`.

If you see `No reply: check wiring and power`, go to **Settings → Pin check** (see
[Settings](#settings)). It tells you whether the target answers on the expected pins, on swapped pins, or
not at all.

---

## The tiles

### Connect

| Row | What it does |
|---|---|
| `Connect` / `Disconnect` | toggles the connection. Connect wakes the target and reads its debug ID. |
| `< Target: auto >` | LEFT/RIGHT choose `auto`, `RP2040`, `RP2350` or `Cortex-M`. Auto detects the chip. Choose a type yourself if auto cannot tell. |
| `Read debug ID` | Shows the debug port's ID in a pop-up (`4C013477` for an RP2350, `0BC12477` for an RP2040). |
| `ID Flash` | identifies flash chip and shows it size e.g. `Winbond 16 MB (EF4018)`. **This resets the target**, because the chip has to be halted first. |


### Flash

Rows: **Choose file**, **Start**, **Erase all**.

- **Choose file** opens the file browser (see Files). The chosen file name is under the Flash tile.
- **Start** erases the target and programs it, then verifies by reading it back. A ring shows
  progress through **Erase, Program, Verify**. When it says **Done**, press OK to dismiss it. The target is
  then reset and starts running the new program. CANCEL while it runs stops it (the flash may then be
  half-written; flash again).
- **Erase all** wipes the whole flash. Afterwards the chip is blank: it will not boot
  and appears on USB as a boot drive. It then checks the first and last 256 bytes of every 64 KB block.
- If no RP2040 or RP2350 is connected, **Start** reads `Test run` and only plays the progress ring with
  nothing written.

Files can be `.uf2` or `.bin`. A `.uf2` carries its own addresses. A `.bin` is written at the start of
the flash. Files are checked before anything is erased: a file that will not fit in the flash is
refused, and an RP2040 also refuses a UF2 built for another chip (`File is for another chip`).

### Files

Browses the badge's own storage and the SD card for `.uf2` and `.bin` files.

- Press OK on a folder to open it, CANCEL to go up. The path is shown at the top, shortened at the front if
  it is long.
- Press OK on a file and you get a **`Use this file?`** screen with its name, size, block count and chip
  family. Press OK again to choose it, or CANCEL to go back to the list. Nothing is chosen until the second OK.
- When you opened Files from Flash, choosing a file takes you straight back to Flash.

### Memory

Shows the target's memory as five rows of `address  word  word` (eight bytes per row, 40 bytes in all).

- UP/DOWN move by 16 bytes, LEFT/RIGHT by 256 bytes.
- OK opens a text box for an address in hex (`20000000`, with or without `0x`). It is rounded down to a
  multiple of 8.
- Useful addresses: `20000000` is RAM, `10000000` is the start of flash.
- The screen is a snapshot taken when you move or enter an address; it does not refresh by itself.
- A row of `????????` means nothing is mapped there (the pop-up says `No memory at ...`). The other rows
  still show. After such an error the app re-establishes the link on its own.
- If the chip is sitting in its bootrom (a blank board, or after a rescue), reading flash would return zeros.
  The tile turns the flash window on for you and tells you `XIP on, core halted`.

### Core

Controls the target's CPU.

| Row | What it does |
|---|---|
| `Halt` | Stops the CPU and shows the **PC** and **SP**. |
| `Resume` | Lets it run again. |
| `Step` | Runs one instruction, then halts and shows the new PC and SP. |
| `Reset` | Resets the target and reconnects. |

**PC** (program counter) is the address of the next instruction. `0000xxxx` is the chip's built-in bootrom,
`10xxxxxx` is your program in flash, `2xxxxxxx` is code in RAM. **SP** (stack pointer) is the top of the
stack in RAM. It only changes when code pushes, pops or enters a function, so stepping through a simple
loop shows the PC moving while the SP stays put.

### Serial

A UART terminal to the target on HS_H (the badge transmits) and HS_I (the badge receives).

List rows:

| Row | What it does |
|---|---|
| `Terminal` | Opens the terminal. |
| `< 115200 baud >` | LEFT/RIGHT choose 9600 to 921600. |
| `< TX HS_H RX HS_I >` | LEFT/RIGHT swap the pair, if your wires are the other way round. |
| `< Auto-record: off >` | When on, opening the terminal starts a recording at once. |
| `< Stamp: off >` | Puts a time at the start of every line of **saved** text (see below). |

In the terminal:

| Button | Does |
|---|---|
| OK | Opens a text box. What you type is sent with a newline added. |
| UP / DOWN | Scroll back and forward through the last 60 lines. |
| LEFT | Clears the screen and the saved-text buffer. |
| RIGHT, **tap** | Saves a **snapshot** of the last 16 KB received. |
| RIGHT, **hold** (about 0.6 s) | Starts or stops **recording** to a file. |

The bottom lines show the baud rate and either `rx <bytes>` or, while recording, `REC <size>` in amber.
The size reads in B, then KB, then MB.

Files go to `/sdcard/serial/` on the SD card (or `/serial/` on the badge's flash if there is no card):

- snapshots: `serial_snap_001.txt`, `serial_snap_002.txt`, ...
- recordings: `serial_001.txt`, `serial_002.txt`, ...

A recording appends everything received as it arrives, with no size limit, and is saved to the card at
least every 2 seconds. A snapshot is the last 16 KB. Both contain the real bytes, with the original line
breaks, not the wrapped lines of the screen. If the card is full or removed, the recording stops with a
pop-up and the terminal keeps working.

**Stamp** adds a time at the start of each saved line. The row shows which kind: `clock` gives
`[2026-10-27 15:30:00] text`, and `elapsed` gives `[+3.165] text` (seconds since the terminal opened). The
badge only knows the real date and time once it has synced over WiFi, so until then you get `elapsed`.
The screen itself is not stamped.

### Settings

| Row | What it does |
|---|---|
| `< SWD max >` | The SWD clock: `max` (about 450 kHz), `fast`, `medium`, `slow`. Use a slower setting if the wires are long or flaky. |
| `< Slot 2 >` | Which badge slot the proto hexpansion is in (1 to 6). Closes any connection. |
| `Rescue DP` | Needs a second OK. A rescue reset for a chip that is stuck (see below). |
| `Pin check` | Tests the wiring (see below). |
| `Slot power` | Needs a second OK. Cuts the slot's power for half a second and restores it. |

**Rescue DP.** On an RP2350 it resets everything except the debug port and leaves both cores in the
bootrom, which frees a chip whose firmware has locked it up. On an RP2040 it does a reset through its
rescue debug port. The app then reconnects. For an RP2040 it also works when Connect has failed; for an RP2350, connect first.
It does not erase the flash.

**Pin check.** Closes the connection and runs three steps, showing each result as it arrives:

- **SWD**: `SWD ok HS_F/HS_G RP2350`, or `SWD: clock and data swapped` (swap those two wires), or
  `SWD answers on HS_x/HS_y` (the wires are on other pads), or `SWD: no reply on any pin pair` (check GND,
  power and that the target is on).
- **UART**: which pin carries the target's output (`target TX on HS_I (default)`, or
  `target TX on HS_H, swap in Serial`), or `UART: silent` if the target is not sending anything. Silence is
  normal for firmware that does not print.
- **LEDs**: lights green, blue, white in turn.

**Slot power.** The proto hexpansion's power LED goes out and comes back. It is useful for a target that is
powered from the badge slot and has hung. It does nothing to a board powered over its own USB cable. The
app refuses to do it for a slot that has an app or storage mounted (for example an SD card hexpansion), with
`Slot N has an app or storage`. The badge's hexpansion manager treats the cycle as a removal followed by a
fresh insertion.

---

## Common jobs

**Flash a program.**
1. Put the `.uf2` or `.bin` on the SD card. Connect (Connect tile).
2. Flash → `Choose file` → find the file → OK, then OK again on `Use this file?`.
3. `Start`. Wait for **Done**, press OK. The target restarts with the new program.

**Check the flash size of a board.** Connect, then Connect → `ID Flash`. It shows the maker and size
(for example `Winbond 8 MB (EF4017)`).

**Wipe a board.** Flash → `Erase all` → OK, OK. Wait for Done.

**Look at what is in memory.** Memory tile → OK → type `10000000` for the start of flash, or `20000000` for RAM.

**Find out where a program is stuck.** Core → `Halt` and read the PC. Press `Step` to move one instruction,
or `Resume` to let it run.

**Watch and save a program's serial output.**
1. Wire HS_H to the target's UART RX and HS_I to its UART TX, with a common GND.
2. Serial → set the baud rate → `Terminal`.
3. Tap RIGHT for a snapshot, or hold RIGHT to start and stop a recording. Set `Auto-record` to on to start
   recording whenever you open the terminal, and `Stamp` to on for times on each line.

**Recover a board that will not connect.**
1. Settings → `Pin check`. Fix whatever it reports.
2. If SWD answers but Connect fails, try Settings → `Rescue DP`, then Connect again.
3. For an RP2040, `Rescue DP` also clears the state where only its rescue port answers.
4. As a last resort, unplug and replug the target's power.

---

## Troubleshooting

| You see | It means | Try |
|---|---|---|
| `No reply: check wiring and power` | Nothing answers on SWD. | Settings → Pin check. Check GND, that the target is powered, and the wire positions. |
| `Rescue DP answered, not a core` | An RP2040 is in a state where only its rescue port answers. | Settings → Rescue DP, then Connect. |
| `Unknown chip, debug ID ...` | Something answers but is not a supported chip. | Pick the Target type by hand (Connect → Target), or check you have the right board. |
| `Target debug will not power up` | The chip's debug domain did not wake. | Power-cycle the target, or try Rescue DP. |
| `Not connected` | An action needs a connection first. | Connect → Connect. |
| `No file selected` | Flash → Start with no file chosen. | Choose a file first. |
| `File does not fit in flash` | The image goes past the end of the chip's flash, or before its start. | Check the file is for this board; `ID Flash` shows the real flash size. |
| `File is for another chip` | A UF2 built for a different chip family. | Use a UF2 for this chip. |
| `File is not in address order` | The UF2's blocks are out of order. | Re-export the UF2. |
| `Verify failed at ...` | What was read back did not match. | Flash again; try a slower SWD speed (Settings). |
| `Erase failed at ...` | A block did not erase. | Try again; check the target's power. |
| `Flash routine timed out` | A bootrom flash call never finished. | Reset the target (Core → Reset) and retry. |
| `Flash chip did not answer` | The flash chip did not reply to its ID command. | Retry; check the board's power. |
| `No memory at ...` | The address is not mapped. | Use an address in RAM (`2000xxxx`) or flash (`10xxxxxx`). |
| `XIP on, core halted` | Information: the chip was in the bootrom, so the app switched flash reads on. | Core → Reset to run again; the flash window will switch off again. |
| `Slot N has an app or storage` | Slot power refused: something is using that slot. | Use the slot your proto hexpansion is in. |
| `Nothing received to save` | The terminal has nothing to save yet. | Wait for data, or check the baud rate and the TX/RX pair. |
| `Log stopped: ...` | Writing the recording failed (card full or removed). | Check the SD card. The terminal still works. |
| Terminal shows `.....` or odd characters | Wrong baud rate. | Change the baud row. |
| Terminal shows nothing | Wrong pair, or the target is silent. | Pin check tells you which pin carries the target's output; or swap the pair in Serial. |

---

## Limits

- Only the first Arm core can be debugged. Core 1 and the RP2350's RISC-V cores are not reachable.
- The flash size is read from the chip, capped at 16 MB.
- Slot power only helps targets powered from the badge slot.
- The badge's clock is unset until it has synced over WiFi, so Serial stamps use seconds since the terminal
  opened until then.
- After a rescue reset, or on a blank chip, reading flash needs it switched on first. The Memory tile does that
  for you and leaves the core halted.
- A target whose core stays halted for a long time may stop answering on its own USB connection. Replug it.
