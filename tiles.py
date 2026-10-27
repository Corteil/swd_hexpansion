"""The tiles other than Files. Connect, Memory and Core drive the SWD link; Flash is a
dry run, and pin check and slot power are still stubs."""
import time

from events.input import BUTTON_TYPES

from . import ui, uf2
from .filepicker import FilesTile
from .leds import RX, TX
from .tile import ListTile, Tile, flasher_for as _flasher

STAGES = ("Erase", "Program", "Verify")
BAUDS = (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600)
SPEEDS = ((0, "max"), (1, "fast"), (4, "medium"), (20, "slow"))  # (delay_us, label); 0 is ~450 kHz


def clock_set():
    """True once the badge knows the real time (over WiFi): until then its clock counts from 2000."""
    return time.localtime()[0] >= 2024


def next_log_path(tag=""):
    """A fresh /sdcard/serial/serial_<tag>NNN.txt, or /serial/... on the badge's flash when no SD card
    is mounted. Creates the folder. Each tag (recordings: none, snapshots: "snap_") counts on its own."""
    import os
    try:
        os.listdir("/sdcard")
        folder = "/sdcard/serial"
    except OSError:
        folder = "/serial"
    try:
        os.mkdir(folder)
    except OSError:
        pass   # already there
    taken = set(os.listdir(folder))
    n = 1
    while "serial_%s%03d.txt" % (tag, n) in taken:
        n += 1
    return "%s/serial_%s%03d.txt" % (folder, tag, n)


def _need_link(app):
    app.note("Not available yet")


def _linked(app):
    """The SWD driver once connected, else a pop-up and None."""
    if app.link.swd is None or app.link.target is None:
        app.note("Not connected")
        return None
    return app.link.swd


class ConnectTile(ListTile):
    key = "connect"
    title = "Connect"
    colour = (0.3, 0.8, 1.0)
    hint = "wake and read IDCODE"

    def summary(self, app):
        t = app.link.target
        return t.name if t else app.link.label()

    def rows(self, app):
        from .targets import TARGETS
        link = app.link
        name = "auto" if link.choice < 0 else TARGETS[link.choice].name
        return ["Disconnect" if link.target else "Connect", "< Target: %s >" % name, "Read debug ID", "ID Flash"]

    def adjust(self, app, i, d):
        from .targets import TARGETS
        if i == 1:
            app.link.choice = (app.link.choice + 1 + d) % (len(TARGETS) + 1) - 1

    def act(self, app, i):
        link = app.link
        if i == 0:
            if link.target:   # one row that toggles
                link.close()
            elif app.attempt(link.connect):
                app.note("Connected: " + link.target.name)
        elif i == 2:
            swd = _linked(app)
            if swd:
                app.attempt(lambda: app.note("Debug ID %08X" % swd.dp_read(0)))
        elif i == 3:
            self._flash_id(app)

    def _flash_id(self, app):
        """Ask the flash chip what it is. Needs a halted, reset chip, so it restarts afterwards."""
        link = app.link
        if _linked(app) is None:
            return
        flasher = _flasher(link)
        if flasher is None:
            app.note("No flash support for this chip")
            return

        def go():
            jedec, _size, text = flasher.probe()
            link.swd.reset()
            time.sleep_ms(300)
            link.connect()
            app.note("%s (%02X%02X%02X)" % ((text,) + jedec))
        app.attempt(go)

    def draw(self, app, ctx):
        super().draw(app, ctx)
        t = app.link.target
        if t and t.flash_size:
            # known once ID Flash, Flash or Erase all has read the chip; the profile's size is only a guess
            known = app.link.flash_text
            ui.text(ctx, known or "flash --", 0, 68, ui.TEXT if known else ui.MUTED)
            if not known:
                ui.text(ctx, "run ID flash", 0, 92, ui.MUTED)


class FlashTile(Tile):
    key = "flash"
    title = "Flash"
    colour = (1.0, 0.5, 0.2)
    hint = "erase, program, verify"

    @property
    def hide_header(self):
        return self.stage >= 0  # the progress ring takes the edge of the screen

    def __init__(self):
        self.cur = 0
        self.stage = -1  # -1 idle, 0..2 running, 3 done
        self.frac = 0.0
        self.gen = None       # Flasher.run() generator while a real flash is running
        self.simulated = False
        self.erasing = False  # the running job is "Erase all", not a flash
        self.armed = False    # Erase all waits for a second OK

    def summary(self, app):
        return ui.clip(app.selected.rsplit("/", 1)[-1], 24) if app.selected else "no file chosen"

    def enter(self, app):
        self.cur = 0
        self.armed = False

    def _progress(self):
        if self.stage < 0:
            return 0.0
        if self.stage >= len(STAGES):
            return 1.0
        if self.erasing:   # erase is most of the work, the spot-check the last tenth
            return self.frac * 0.9 if self.stage == 0 else 0.9 + self.frac * 0.1
        return (self.stage + self.frac) / len(STAGES)

    def _start_erase(self, app):
        flasher = _flasher(app.link)
        if flasher is None:
            app.note("Not connected")
            return
        self.stage, self.frac, self.simulated, self.erasing = 0, 0.0, False, True
        self.gen = flasher.erase_all()

    def _start(self, app):
        flasher = _flasher(app.link)
        self.stage, self.frac, self.gen, self.erasing = 0, 0.0, None, False
        self.simulated = flasher is None
        if flasher is not None:
            self.gen = flasher.run(app.selected)

    def _step(self, app):
        """Advance the real flash by one unit of work (about a sector)."""
        try:
            self.stage, self.frac = next(self.gen)
        except StopIteration:
            self.gen = None
        except Exception as e:  # FlashError, SWDError, a bad file
            self._stop(app)
            app.note(str(e)[:40] or type(e).__name__)
            return
        if self.stage >= len(STAGES):
            self.gen = None
            self._run_image(app)

    def _stop(self, app):
        if self.gen is not None:
            try:
                self.gen.close()  # puts the flash back into XIP mode
            except Exception:
                pass
        self.gen = None
        self.stage = -1

    def _run_image(self, app):
        """Reset so the new image starts, then pick the link up again."""
        link = app.link

        def go():
            link.swd.release_core1()
            link.swd.reset()
            time.sleep_ms(300)
            link.connect()
        app.attempt(go)

    def update(self, app, b, delta):
        if 0 <= self.stage < len(STAGES):  # running
            if self.gen is not None:
                self._step(app)
            else:
                # Dry run: stands in for the SWD work so the progress UI can be seen with no target.
                self.frac += delta / 2000
                if self.frac >= 1:
                    self.frac = 0.0
                    self.stage += 1
            return
        if self.stage >= len(STAGES):   # the "Done" screen: OK dismisses it, nothing starts behind it
            if b.pressed(BUTTON_TYPES["CONFIRM"]):
                self.stage, self.erasing = -1, False
            return
        if b.pressed(BUTTON_TYPES["DOWN"]):
            self.cur, self.armed = (self.cur + 1) % 3, False
        elif b.pressed(BUTTON_TYPES["UP"]):
            self.cur, self.armed = (self.cur - 1) % 3, False
        elif b.pressed(BUTTON_TYPES["CONFIRM"]):
            if self.cur == 2:
                if self.armed:
                    self.armed = False
                    self._start_erase(app)
                else:
                    self.armed = True
            elif self.cur == 0:
                app.open_picker("flash")
            elif not app.selected:
                app.note("No file selected")
            elif app.attempt(lambda: uf2.check(app.selected)):  # reject a damaged file before touching the target
                self._start(app)

    def back(self, app):
        if self.stage >= 0:
            self._stop(app)  # abort, or dismiss the "Done" screen
            return True
        return False

    def draw(self, app, ctx):
        running = 0 <= self.stage < len(STAGES)
        if self.stage >= 0:
            ui.ring(ctx, self._progress(), ui.OK if self.stage >= len(STAGES) else self.colour)
        ui.icon(ctx, "flash", 0, -62, 16, self.colour)
        if self.erasing and self.stage >= 0:   # erasing has nothing to do with the chosen file
            ui.text(ctx, "Erase all", 0, -30, ui.TEXT)
        else:
            name = app.selected.rsplit("/", 1)[-1] if app.selected else "no file chosen"
            ui.text(ctx, name, 0, -30, ui.TEXT if app.selected else ui.MUTED, tail=7)
        if running:
            stage = "Check" if self.erasing and self.stage == 2 else STAGES[self.stage]
            ui.text(ctx, "%s  %d%%" % (stage, int(self.frac * 100)), 0, 0, self.colour, ui.label_font_size)
            ui.bar(ctx, -70, 18, 140, 8, self.frac, self.colour)
            ui.text(ctx, "test run, nothing written" if self.simulated else "Cancel to stop", 0, 44, ui.MUTED)
        elif self.stage >= len(STAGES):
            ui.text(ctx, "Done", 0, 0, ui.OK, ui.label_font_size)
            ui.text(ctx, "test run, nothing written" if self.simulated else
                    ("Erased, target restarted" if self.erasing else "Verified, target restarted"), 0, 28, ui.MUTED)
        else:
            linked = bool(app.link.target and app.link.target.name in ("RP2350", "RP2040"))
            erase = "OK to confirm erase" if self.armed else "Erase all"
            for i, label in enumerate(("Choose file", "Start" if linked else "Test run", erase)):
                y = -6 + i * 24
                if i == self.cur:
                    ctx.rgb(*ui.DIM).rectangle(-70, y - 11, 140, 22).fill()
                ui.text(ctx, label, 0, y, ui.TEXT if i == self.cur else ui.MUTED)


class MemoryTile(Tile):
    key = "memory"
    title = "Memory"
    colour = (0.7, 0.5, 1.0)
    hint = "peek and poke"

    def __init__(self):
        self.addr = 0x20000000
        self.rows = None   # what _load read, see there
        self._app = None

    def summary(self, app):
        return "%08X" % self.addr

    def update(self, app, b, delta):
        if b.pressed(BUTTON_TYPES["CONFIRM"]):
            app.ask_text("Address (hex)", "%08x" % self.addr, self._set_addr)
            return
        for name, step in (("DOWN", 0x10), ("UP", -0x10), ("RIGHT", 0x100), ("LEFT", -0x100)):
            if b.pressed(BUTTON_TYPES[name]):
                self.addr = (self.addr + step) & 0xFFFFFFF0
                self._load(app)
                return

    def _load(self, app):
        """Read the 10 words on screen, a row (2 words) at a time so one unreadable row, a window
        that starts below RAM for instance, does not blank the rows that are fine. Done on a
        change, never per frame."""
        swd = app.link.swd if app.link.target else None
        self.rows = None   # None: no link. Else one entry per row: (word, word), or None if unreadable
        if not swd:
            return

        def read():
            if 0x10000000 <= self.addr < 0x20000000 and app.link.ensure_xip():
                # reads of flash return junk while the chip sits in the bootrom
                app.note("XIP on, core halted")
            rows, bad = [], None
            for r in range(5):
                a = (self.addr + r * 8) & 0xFFFFFFFF
                try:
                    rows.append(tuple(swd.read_block(a, 2)))
                except Exception:   # SWDError: nothing is mapped there
                    rows.append(None)
                    bad = bad if bad is not None else a
                    # An unmapped read leaves the target not answering at all, good addresses
                    # included, until the link is brought up again.
                    try:
                        app.link.connect()
                    except Exception:
                        rows.extend([None] * (4 - r))   # the link is gone: leave the rest unread
                        break
            self.rows = rows
            if bad is not None:
                app.note("No memory at %08X" % bad)
        app.attempt(read)

    def _set_addr(self, s):
        try:
            self.addr = int(s.strip().lower().replace("0x", ""), 16) & 0xFFFFFFF8
            self._load(self._app)
        except ValueError:
            self._app.note("Enter a hex address")

    def enter(self, app):
        self._app = app
        self._load(app)

    def draw(self, app, ctx):
        ui.icon(ctx, "memory", 0, -70, 12, self.colour)
        for r in range(5):
            a = self.addr + r * 8
            w = self.rows[r] if self.rows else None
            if w:
                cells = ("%08X" % w[0], "%08X" % w[1])
            else:   # dashes: no link yet; question marks: this row could not be read
                cells = ("--------",) * 2 if self.rows is None else ("????????",) * 2
            ui.text(ctx, "%08X  %s  %s" % ((a & 0xFFFFFFFF,) + cells), 0, -38 + r * 20, ui.TEXT if r == 0 else ui.MUTED, ui.tiny_font_size * 0.85)
        ui.text(ctx, "OK to enter address", 0, 70, ui.MUTED)


class CoreTile(ListTile):
    key = "core"
    title = "Core"
    colour = (0.4, 0.9, 0.5)
    hint = "halt, step, reset"

    def __init__(self):
        super().__init__()
        self.regs = None  # (pc, sp) while halted

    def enter(self, app):
        super().enter(app)
        self.regs = None

    def rows(self, app):
        return ["Halt", "Resume", "Step", "Reset"]

    def _read_regs(self, swd):
        self.regs = (swd.read_reg(15), swd.read_reg(13))

    def act(self, app, i):
        swd = _linked(app)
        if not swd:
            return
        link = app.link

        def run():
            self.regs = None
            if i == 0:
                swd.halt()
            elif i == 1:
                swd.resume()
            elif i == 2:
                swd.step()
                swd.halt()
            else:
                swd.reset()
                time.sleep_ms(50)
                link.connect()
                return
            link.refresh()
            if link.state == "halted":
                self._read_regs(swd)
        app.attempt(run)

    def draw(self, app, ctx):
        super().draw(app, ctx)
        if self.regs:
            ui.text(ctx, "PC %08X" % self.regs[0], 0, 66, ui.TEXT)
            ui.text(ctx, "SP %08X" % self.regs[1], 0, 82, ui.MUTED)


class SerialTile(ListTile):
    key = "serial"
    title = "Serial"
    colour = (0.3, 0.9, 0.9)
    hint = "UART terminal"
    LINES = 7       # lines on screen
    WIDTH = 26      # characters per line
    KEEP = 60       # lines of scrollback
    RAWMAX = 16384  # bytes kept for Save: the received data as it arrived, newest last
    LONG_MS = 600   # RIGHT held this long starts or stops recording; a shorter press saves a snapshot

    def __init__(self):
        super().__init__()
        self.baud = 4
        self.swap = False   # False: badge TX = HS_H, RX = HS_I
        self.uart = None
        self.term = False   # showing the terminal rather than the list
        self.lines = [""]
        self.scroll = 0     # lines above the newest
        self.rx_bytes = 0
        self.raw = bytearray()   # what Save writes: the real bytes, not the wrapped screen lines
        self.stamp = False       # put a time at the start of every line of the saved text
        self._bol = True         # the next received byte starts a line
        self._t0 = 0             # ticks_ms when the terminal opened: stamps count from here without a clock
        self.logging = False     # the "long save": append everything received to a file as it arrives
        self.logfile = None
        self.logged = 0          # bytes written to it
        self._flush_at = 0
        self._rhold = 0          # ms RIGHT has been held, 0 when it is up
        self._rfired = False     # the long press already acted, so the release must not also save

    def summary(self, app):
        return "%d baud" % BAUDS[self.baud]

    def rows(self, app):
        return ["Terminal", "< %d baud >" % BAUDS[self.baud],
                "< TX HS_%s RX HS_%s >" % (("I", "H") if self.swap else ("H", "I")),
                "< Auto-record: %s >" % ("on" if self.logging else "off"),
                "< Stamp: %s >" % (("clock" if clock_set() else "elapsed") if self.stamp else "off")]

    def adjust(self, app, i, d):
        if i == 1:
            self.baud = (self.baud + d) % len(BAUDS)
        elif i == 2:
            self.swap = not self.swap
        elif i == 3:
            self.logging = not self.logging
        elif i == 4:
            self.stamp = not self.stamp

    def act(self, app, i):
        if i == 0:
            app.attempt(lambda: self._open(app))
        elif i == 3:
            self.logging = not self.logging
        elif i == 4:
            self.stamp = not self.stamp

    def _open(self, app):
        from machine import UART
        from system.hexpansion.config import HexpansionConfig
        app.link.open()   # claims the LEDs
        pins = HexpansionConfig(app.link.port).pin
        tx, rx = (pins[3], pins[2]) if self.swap else (pins[2], pins[3])
        self._close()
        self.uart = UART(1, BAUDS[self.baud], tx=tx, rx=rx, rxbuf=1024, timeout=0)
        self.term, self.scroll = True, 0
        self._rhold, self._rfired = 0, False
        self._bol, self._t0 = True, time.ticks_ms()
        if self.logging:
            self._start_log(app)

    def _close(self):
        self._stop_log()
        if self.uart is not None:
            self.uart.deinit()
            self.uart = None

    def _stamp(self):
        """The text put before a line: the date and time once the badge knows them, else the seconds
        since the terminal was opened."""
        if clock_set():
            t = time.localtime()
            return b"[%04d-%02d-%02d %02d:%02d:%02d] " % tuple(t[:6])
        return b"[+%.3f] " % (time.ticks_diff(time.ticks_ms(), self._t0) / 1000)

    def _stamped(self, data):
        """data with a stamp inserted at the start of each line, whichever chunk the line arrives in.
        Blank lines are left bare."""
        if not self.stamp:
            return data
        if not self._bol and b"\n" not in data:   # the middle of a line: nothing to insert
            return data
        stamp = self._stamp()
        out = bytearray()
        for c in data:
            if self._bol and c != 10 and c != 13:
                out.extend(stamp)
                self._bol = False
            out.append(c)
            if c == 10:
                self._bol = True
        return bytes(out)

    def _start_log(self, app):
        """Open a new numbered file; from now on everything received is appended to it."""
        try:
            path = next_log_path()
            self.logfile = open(path, "wb")
        except Exception as e:   # no space, a read-only card
            self.logging, self.logfile = False, None
            app.note("Log off: " + (str(e)[:24] or type(e).__name__))
            return
        self.logged = 0
        self._flush_at = time.ticks_ms()
        app.note("Recording to " + path.rsplit("/", 1)[-1])   # the folder would not fit in the pop-up

    def _stop_log(self):
        if self.logfile is not None:
            try:
                self.logfile.close()   # flushes what is still buffered
            except Exception:
                pass
            self.logfile = None

    def _log(self, app, data):
        try:
            self.logfile.write(data)
            self.logged += len(data)
            if time.ticks_diff(time.ticks_ms(), self._flush_at) > 2000:   # at most 2 s of data at risk
                self.logfile.flush()
                self._flush_at = time.ticks_ms()
        except Exception as e:   # card removed or full: say so and stop rather than lose the terminal
            self._stop_log()
            self.logging = False
            app.note("Log stopped: " + (str(e)[:20] or type(e).__name__))

    def back(self, app):
        if self.term:
            self.term = False
            self._close()
            return True
        return False

    def _feed(self, data):
        """Append received bytes to the scrollback, wrapping and honouring CR / LF / backspace."""
        for c in data:
            if c == 10:
                self.lines.append("")
            elif c == 8 or c == 127:
                self.lines[-1] = self.lines[-1][:-1]
            elif c == 13:
                continue
            elif 32 <= c < 127 or c == 9:
                if len(self.lines[-1]) >= self.WIDTH:
                    self.lines.append("")
                self.lines[-1] += chr(c) if c != 9 else " "
            else:
                self.lines[-1] += "."   # non-text byte: a hint something arrived, e.g. wrong baud
                if len(self.lines[-1]) > self.WIDTH:
                    self.lines.append("")
        del self.lines[:-self.KEEP]
        if self.scroll:
            self.scroll = min(self.scroll + data.count(b"\n"), len(self.lines) - 1)

    def update(self, app, b, delta):
        if not self.term:
            super().update(app, b, delta)
            return
        try:
            data = self.uart.read()
            if data:
                self.rx_bytes += len(data)
                text = self._stamped(data)   # what the saved text holds; the screen shows the bare data
                self.raw.extend(text)
                if len(self.raw) > self.RAWMAX:
                    self.raw = self.raw[-self.RAWMAX:]   # a bytearray cannot delete a slice here
                if self.logfile is not None:
                    self._log(app, text)
                self._feed(data)
                app.link.leds.pulse(RX)
        except Exception as e:   # whatever goes wrong with the data must not take the buttons down too
            app.note("Serial: " + (str(e)[:30] or type(e).__name__))
        if b.pressed(BUTTON_TYPES["CONFIRM"]):
            app.ask_text("Send (+ newline)", "", lambda t: self._send(app, t))
        elif b.pressed(BUTTON_TYPES["UP"]):
            self.scroll = min(self.scroll + 1, max(0, len(self.lines) - self.LINES))
        elif b.pressed(BUTTON_TYPES["DOWN"]):
            self.scroll = max(0, self.scroll - 1)
        elif b.pressed(BUTTON_TYPES["LEFT"]):
            self.lines, self.scroll = [""], 0
            self.raw, self._bol = bytearray(), True
        self._right(app, b, delta)

    def _right(self, app, b, delta):
        """RIGHT: held for LONG_MS starts or stops recording; released sooner it saves a snapshot.
        The snapshot is decided on release, so a long press does not also save one."""
        if b.get(BUTTON_TYPES["RIGHT"]):
            self._rhold += delta
            if self._rhold >= self.LONG_MS and not self._rfired:
                self._rfired = True
                if self.logfile is not None:
                    n = self.logged
                    self._stop_log()
                    app.note("Recording stopped, %d bytes" % n)
                else:
                    self._start_log(app)
        else:
            if self._rhold and not self._rfired:
                self._save(app)
            self._rhold, self._rfired = 0, False

    def _save(self, app):
        """Write everything received so far (up to the last RAWMAX bytes) to a new numbered file."""
        if not self.raw:
            app.note("Nothing received to save")
            return

        def write():
            path = next_log_path("snap_")
            with open(path, "wb") as f:
                f.write(self.raw)
            app.note("Saved %d bytes to %s" % (len(self.raw), path))
        app.attempt(write)

    def _send(self, app, text):
        if self.uart is not None:
            self.uart.write(text.encode() + b"\r\n")
            app.link.leds.pulse(TX)

    def draw(self, app, ctx):
        if not self.term:
            super().draw(app, ctx)
            return
        end = len(self.lines) - self.scroll
        for r, line in enumerate(self.lines[max(0, end - self.LINES):end]):
            ui.text(ctx, line or " ", 0, -52 + r * 17, ui.TEXT if r == min(end, self.LINES) - 1 else ui.MUTED,
                    ui.tiny_font_size * 0.9)
        if self.logfile is not None:   # recording: show how much has gone to the file
            n = self.logged
            size = "%d B" % n if n < 10240 else "%d KB" % (n >> 10) if n < 1 << 20 else "%.1f MB" % (n / (1 << 20))
            status = "%d baud  REC %s" % (BAUDS[self.baud], size)
            ui.text(ctx, status, 0, 74, ui.WARN, ui.tiny_font_size * 0.85)
        else:
            ui.text(ctx, "%d baud  rx %d" % (BAUDS[self.baud], self.rx_bytes), 0, 74, self.colour, ui.tiny_font_size * 0.85)
        ui.text(ctx, "OK send <clear >save", 0, 88, ui.MUTED, ui.tiny_font_size * 0.85)
        ui.text(ctx, "hold > rec", 0, 101, ui.MUTED, ui.tiny_font_size * 0.85)


class SettingsTile(ListTile):
    key = "settings"
    title = "Settings"
    colour = (0.8, 0.8, 0.85)
    hint = "speed, port, rescue, power"

    def __init__(self):
        super().__init__()
        self.armed = None   # index of the row waiting for a second OK (rescue, slot power)
        self.check = None   # the Pin check generator while its results are on screen
        self.lines = []     # result lines of the Pin check: (text, ok)

    def enter(self, app):
        super().enter(app)
        self.armed = None
        self._stop_check()

    def _stop_check(self):
        if self.check is not None:
            try:
                self.check.close()   # puts the LEDs back off
            except Exception:
                pass
        self.check = None
        self.lines = []

    def _start_check(self, app):
        """Release the pins first: the check drives them itself, so any connection is closed."""
        from . import pincheck
        app.link.close()
        self.lines = [("starting...", None)]
        self.check = pincheck.run(app.link.port)

    def update(self, app, b, delta):
        if self.check is None and not self.lines:
            super().update(app, b, delta)
            return
        if self.check is not None:   # one step per frame; each step can take a second or two
            try:
                self.lines = next(self.check)
            except StopIteration:
                self.check = None
            except Exception as e:
                self.check = None
                self.lines.append((str(e)[:30] or type(e).__name__, False))

    def back(self, app):
        if self.check is not None or self.lines:
            self._stop_check()
            return True
        return False

    def _speed(self, app):
        for i, (us, _name) in enumerate(SPEEDS):
            if us == app.link.delay_us:
                return i
        return 2

    def rows(self, app):
        return ["< SWD %s >" % SPEEDS[self._speed(app)][1],
                "< Slot %d >" % app.link.port,
                "OK to confirm rescue" if self.armed == 2 else "Rescue DP",
                "Pin check", "OK to cycle power" if self.armed == 4 else "Slot power"]

    def adjust(self, app, i, d):
        link = app.link
        if i == 0:
            link.delay_us = SPEEDS[(self._speed(app) + d) % len(SPEEDS)][0]
            if link.swd:
                link.swd.delay_us = link.delay_us
        elif i == 1:
            link.close()  # the pins belong to the old slot
            link.port = (link.port - 1 + d) % 6 + 1

    def _rescue(self, app):
        """Rescue reset. RP2350: both cores end up in the bootrom. RP2040: a reset through the
        rescue DP, which also frees a chip whose core DPs have stopped answering. Works on
        whatever the debug port still answers, so a locked-up or hung chip is fine; the link is
        rebuilt afterwards."""
        link = app.link
        known = link.swd is not None and link.target is not None
        name = link.target.name if known else None
        if known and name not in ("RP2350", "RP2040"):
            app.note("Rescue is RP2040/RP2350 only")
            return

        def go():
            if name == "RP2350":
                link.swd.rescue()
                time.sleep_ms(200)
            else:   # an RP2040, or a chip that does not answer: try its rescue DP
                link.open()
                link.swd.rescue_rp2040()
            link.connect()
        if app.attempt(go):
            app.note("Rescued: " + link.target.name)

    def _slot_power(self, app):
        """Cut the slot's power for half a second and restore it. The slot's detect pin doubles as
        its power switch: the firmware's own Power Off screen drives it high to switch hexpansions
        off, and the hexpansion manager sees the rise as a removal and the release as a fresh
        insertion. Slots the manager has storage or an app on are refused: cutting an SD card's
        power mid-write, or an app's, is not something to do from here."""
        import tildagonos
        from egpio import ePin
        link = app.link
        port = link.port
        try:
            from system.hexpansion.app import _hexpansion_manager as manager
        except ImportError:
            manager = None
        if manager is not None and (port in manager.mountpoints or port in manager.hexpansion_apps):
            app.note("Slot %d has an app or storage" % port)
            return
        detect = (tildagonos.EPIN_ND_A, tildagonos.EPIN_ND_B, tildagonos.EPIN_ND_C,
                  tildagonos.EPIN_ND_D, tildagonos.EPIN_ND_E, tildagonos.EPIN_ND_F)[port - 1]
        was_linked = link.target is not None
        link.close()   # its pins lose power too
        pin = ePin(detect, ePin.OUT)
        try:
            pin.on()
            time.sleep_ms(500)
        finally:
            pin.init(ePin.IN)   # back to a detect input; the target is powered again
        time.sleep_ms(300)
        if was_linked and app.attempt(link.connect):
            app.note("Slot %d power cycled, reconnected" % port)
        else:
            app.note("Slot %d power cycled" % port)

    def act(self, app, i):
        if i in (2, 4):
            if self.armed == i:
                self.armed = None
                if i == 2:
                    self._rescue(app)
                else:
                    self._slot_power(app)
            else:
                self.armed = i
            return
        self.armed = None
        if i == 3:
            self._start_check(app)

    def draw(self, app, ctx):
        if self.check is None and not self.lines:
            super().draw(app, ctx)
            return
        ui.icon(ctx, self.key, 0, -70, 16, self.colour)
        for k, (text, ok) in enumerate(self.lines):
            colour = ui.OK if ok else ui.WARN if ok is False else ui.MUTED
            ui.text(ctx, text, 0, -38 + k * 20, colour)
        if self.check is None:
            ui.text(ctx, "Cancel to close", 0, 78, ui.MUTED)


TILES = [ConnectTile(), FlashTile(), FilesTile(), MemoryTile(), CoreTile(), SerialTile(), SettingsTile()]
