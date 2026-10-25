"""The tiles other than Files. Connect, Memory and Core drive the SWD link; Flash is a
dry run, and Serial, rescue, pin check and slot power are still stubs."""
import time

from events.input import BUTTON_TYPES

from . import ui, uf2
from .filepicker import FilesTile
from .tile import ListTile, Tile

STAGES = ("Erase", "Program", "Verify")
BAUDS = (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600)
SPEEDS = ((0, "max"), (1, "fast"), (4, "medium"), (20, "slow"))  # (delay_us, label); 0 is ~450 kHz


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
        return ["Connect", "< Target: %s >" % name, "Read debug ID", "Disconnect"]

    def adjust(self, app, i, d):
        from .targets import TARGETS
        if i == 1:
            app.link.choice = (app.link.choice + 1 + d) % (len(TARGETS) + 1) - 1

    def act(self, app, i):
        link = app.link
        if i == 0:
            if app.attempt(link.connect):
                app.note("Connected: " + link.target.name)
        elif i == 2:
            swd = _linked(app)
            if swd:
                app.attempt(lambda: app.note("Debug ID %08X" % swd.dp_read(0)))
        elif i == 3:
            link.close()

    def draw(self, app, ctx):
        super().draw(app, ctx)
        t = app.link.target
        if t:
            ui.text(ctx, "flash %08X" % t.flash, 0, 70, ui.MUTED)


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
        link = app.link
        if not (link.swd and link.target and link.target.name == "RP2350"):
            app.note("Not connected")
            return
        from .flash_rp2350 import Flasher
        self.stage, self.frac, self.simulated, self.erasing = 0, 0.0, False, True
        self.gen = Flasher(link.swd, link.target.flash_size).erase_all()

    def _start(self, app):
        link = app.link
        self.stage, self.frac, self.gen, self.erasing = 0, 0.0, None, False
        self.simulated = not (link.swd and link.target and link.target.name == "RP2350")
        if not self.simulated:
            from .flash_rp2350 import Flasher
            self.gen = Flasher(link.swd, link.target.flash_size).run(app.selected)

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
            linked = bool(app.link.target and app.link.target.name == "RP2350")
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

    def __init__(self):
        super().__init__()
        self.baud = 5

    def summary(self, app):
        return "%d baud" % BAUDS[self.baud]

    def rows(self, app):
        return ["Terminal", "< %d baud >" % BAUDS[self.baud], "Bridge to USB"]

    def adjust(self, app, i, d):
        if i == 1:
            self.baud = (self.baud + d) % len(BAUDS)

    def act(self, app, i):
        if i != 1:
            _need_link(app)


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

    def act(self, app, i):
        if i == 2:
            if self.armed:
                self.armed = False
                _need_link(app)
            else:
                self.armed = True
            return
        self.armed = False
        if i > 2:
            _need_link(app)


TILES = [ConnectTile(), FlashTile(), FilesTile(), MemoryTile(), CoreTile(), SerialTile(), SettingsTile()]
