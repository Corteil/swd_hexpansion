"""The tiles other than Files. Connect, Memory and Core drive the SWD link; Flash is a
dry run, and pin check and slot power are still stubs."""
import time

from events.input import BUTTON_TYPES

from . import ui, uf2
from .filepicker import FilesTile
from .leds import RX, TX
from .tile import ListTile, Tile

STAGES = ("Erase", "Program", "Verify")
BAUDS = (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600)
SPEEDS = ((0, "max"), (1, "fast"), (4, "medium"), (20, "slow"))  # (delay_us, label); 0 is ~450 kHz


def _need_link(app):
    app.note("Not available yet")


def _flasher(link):
    """A Flasher for the connected chip, or None when there is no flash algorithm for it."""
    if not (link.swd and link.target):
        return None
    if link.target.name == "RP2350":
        from .flash_rp2350 import Flasher
    elif link.target.name == "RP2040":
        from .flash_rp2040 import Flasher
    else:
        return None
    flasher = Flasher(link.swd, link.target.flash_size)
    flasher.on_chip = lambda text: setattr(link, "flash_text", text)   # the Connect tile shows it
    return flasher


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
        self.words = None  # last block read, or None
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
        """Read the 10 words on screen. Done on a change, never per frame."""
        swd = app.link.swd if app.link.target else None
        self.words = None
        if swd:
            def read():
                self.words = swd.read_block(self.addr, 10)
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
            w = self.words
            cells = ("%08X" % w[r * 2], "%08X" % w[r * 2 + 1]) if w else ("--------", "--------")
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

    def __init__(self):
        super().__init__()
        self.baud = 4
        self.swap = False   # False: badge TX = HS_H, RX = HS_I
        self.uart = None
        self.term = False   # showing the terminal rather than the list
        self.lines = [""]
        self.scroll = 0     # lines above the newest
        self.rx_bytes = 0

    def summary(self, app):
        return "%d baud" % BAUDS[self.baud]

    def rows(self, app):
        return ["Terminal", "< %d baud >" % BAUDS[self.baud],
                "< TX HS_%s RX HS_%s >" % (("I", "H") if self.swap else ("H", "I"))]

    def adjust(self, app, i, d):
        if i == 1:
            self.baud = (self.baud + d) % len(BAUDS)
        elif i == 2:
            self.swap = not self.swap

    def act(self, app, i):
        if i == 0:
            app.attempt(lambda: self._open(app))

    def _open(self, app):
        from machine import UART
        from system.hexpansion.config import HexpansionConfig
        app.link.open()   # claims the LEDs
        pins = HexpansionConfig(app.link.port).pin
        tx, rx = (pins[3], pins[2]) if self.swap else (pins[2], pins[3])
        self._close()
        self.uart = UART(1, BAUDS[self.baud], tx=tx, rx=rx, rxbuf=1024, timeout=0)
        self.term, self.scroll = True, 0

    def _close(self):
        if self.uart is not None:
            self.uart.deinit()
            self.uart = None

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
        except Exception as e:
            app.note(str(e)[:40])
            data = None
        if data:
            self.rx_bytes += len(data)
            self._feed(data)
            app.link.leds.pulse(RX)
        if b.pressed(BUTTON_TYPES["CONFIRM"]):
            app.ask_text("Send (+ newline)", "", lambda t: self._send(app, t))
        elif b.pressed(BUTTON_TYPES["UP"]):
            self.scroll = min(self.scroll + 1, max(0, len(self.lines) - self.LINES))
        elif b.pressed(BUTTON_TYPES["DOWN"]):
            self.scroll = max(0, self.scroll - 1)
        elif b.pressed(BUTTON_TYPES["LEFT"]):
            self.lines, self.scroll = [""], 0

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
        ui.text(ctx, "%d baud  rx %d" % (BAUDS[self.baud], self.rx_bytes), 0, 74, self.colour, ui.tiny_font_size * 0.85)
        ui.text(ctx, "OK send  LEFT clear", 0, 88, ui.MUTED, ui.tiny_font_size * 0.85)


class SettingsTile(ListTile):
    key = "settings"
    title = "Settings"
    colour = (0.8, 0.8, 0.85)
    hint = "speed, port, rescue"

    def __init__(self):
        super().__init__()
        self.armed = False

    def enter(self, app):
        super().enter(app)
        self.armed = False

    def _speed(self, app):
        for i, (us, _name) in enumerate(SPEEDS):
            if us == app.link.delay_us:
                return i
        return 2

    def rows(self, app):
        return ["< SWD %s >" % SPEEDS[self._speed(app)][1],
                "< Slot %d >" % app.link.port,
                "OK to confirm rescue" if self.armed else "Rescue DP",
                "Pin check", "Slot power"]

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

    def act(self, app, i):
        if i == 2:
            if self.armed:
                self.armed = False
                self._rescue(app)
            else:
                self.armed = True
            return
        self.armed = False
        if i > 2:
            _need_link(app)


TILES = [ConnectTile(), FlashTile(), FilesTile(), MemoryTile(), CoreTile(), SerialTile(), SettingsTile()]
