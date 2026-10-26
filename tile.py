"""Base classes shared by the tiles, and the link state they all show."""
from . import ui


class Link:
    """The connection to the target, and what the header shows about it."""

    def __init__(self):
        self.state = "no link"  # no link | connected | halted | running
        self.idcode = None
        self.target = None  # the targets.Target in use
        self.choice = -1    # index into TARGETS, or -1 to detect
        self.port = 2       # hexpansion slot with the proto board (slot B)
        self.delay_us = 0   # SWD half-clock delay: 0 = fastest (~450 kHz), stable on short bench wires
        self.swd = None
        self.leds = None
        self.flash_text = None  # "Winbond 16 MB" once ID Flash has read the chip

    def label(self):
        """Short enough for the top edge of the round display."""
        if self.target is not None:
            return "%s %s" % (self.target.name, self.state)
        return self.state

    def colour(self):
        return {"connected": ui.OK, "running": ui.OK, "halted": ui.WARN}.get(self.state, ui.MUTED)

    def open(self):
        """Claim the pins and LEDs. Done on first use so the pins stay untouched until then."""
        if self.swd is None:
            from system.hexpansion.config import HexpansionConfig
            from .leds import Leds
            from .swd_rp2350 import SWD
            self.swd = SWD.from_port(self.port, self.delay_us)
            self.leds = Leds(HexpansionConfig(self.port).ls_pin)

    def close(self):
        if self.swd is not None:
            self.swd.disable()
            self.leds.off()
        self.swd = self.leds = self.target = self.idcode = self.flash_text = None
        self.state = "no link"

    def connect(self):
        from .targets import TARGETS
        self.open()
        self.swd.delay_us = self.delay_us
        candidates = TARGETS if self.choice < 0 else [TARGETS[self.choice]]
        error = None
        for t in candidates:
            try:
                dpidr = self.swd.connect(t.targetsel)
            except Exception as e:  # SWDError, or the pins misbehaving
                error = e
                continue
            if self.choice < 0 and t.dpidr is not None and dpidr != t.dpidr:
                error = ValueError("Unknown chip, debug ID %08X" % dpidr)
                continue
            self.swd.mem_setup(t.ap_base, t.adiv)
            self.target, self.idcode = t, dpidr
            self.refresh()
            return
        self.state, self.idcode, self.target = "no link", None, None
        raise error

    def refresh(self):
        self.state = "halted" if self.swd.is_halted() else "running"


class Tile:
    key = "tile"  # also the icon name
    title = "Tile"
    colour = ui.TEXT
    hint = ""

    def summary(self, app):
        """One line shown under the title on the carousel."""
        return self.hint

    def enter(self, app):
        pass

    def update(self, app, b, delta):
        pass

    def back(self, app):
        """CANCEL pressed. Return True if handled (went up a level), False to leave the tile."""
        return False

    def draw(self, app, ctx):
        pass


class ListTile(Tile):
    """A tile that is a short list of actions. LEFT/RIGHT adjust the highlighted row."""
    ROW_Y = -34
    ROW_H = 24

    def __init__(self):
        self.cur = 0

    def rows(self, app):
        return []

    def act(self, app, i):
        pass

    def adjust(self, app, i, d):
        pass

    def enter(self, app):
        self.cur = 0

    def update(self, app, b, delta):
        from events.input import BUTTON_TYPES
        n = len(self.rows(app))
        if b.pressed(BUTTON_TYPES["DOWN"]):
            self.cur = (self.cur + 1) % n
        elif b.pressed(BUTTON_TYPES["UP"]):
            self.cur = (self.cur - 1) % n
        elif b.pressed(BUTTON_TYPES["CONFIRM"]):
            self.act(app, self.cur)
        elif b.pressed(BUTTON_TYPES["RIGHT"]):
            self.adjust(app, self.cur, 1)
        elif b.pressed(BUTTON_TYPES["LEFT"]):
            self.adjust(app, self.cur, -1)

    def draw(self, app, ctx):
        ui.icon(ctx, self.key, 0, -70, 16, self.colour)
        for i, label in enumerate(self.rows(app)):
            y = self.ROW_Y + i * self.ROW_H
            if i == self.cur:
                ctx.rgb(*ui.DIM).rectangle(-90, y - 11, 180, 22).fill()
            ui.text(ctx, ui.clip(label, 22), 0, y, ui.TEXT if i == self.cur else ui.MUTED)
