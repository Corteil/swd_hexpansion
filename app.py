"""SWD programmer for an RP2350 hexpansion, on the Tildagon badge.

Seven tiles in a carousel: LEFT/RIGHT scroll (wrapping round), CONFIRM opens a
tile, CANCEL goes back a level and, on the carousel, sends the app to the
background. A USB keyboard works everywhere the badge buttons do (its arrows,
Enter and Escape alias to them), types into text fields, and a letter jumps
to the next file starting with it in the file explorer.

License: MIT
"""
import app

from app_components.dialog import TextDialog
from app_components.tokens import clear_background
from events.input import Buttons, BUTTON_TYPES, ButtonDownEvent
from system.eventbus import eventbus

from . import ui
from .leds import SWD as LED_SWD
from .tile import Link
from .tiles import TILES

try:
    from events.keyboard import KEYBOARD_BUTTONS
except ImportError:
    KEYBOARD_BUTTONS = {}

SLIDE_MS = 180
TOAST_MS = 2500


class SWDApp(app.App):
    def __init__(self):
        super().__init__()
        self.buttons = Buttons(self)
        self.keyboard = KEYBOARD_BUTTONS
        self.link = Link()
        self.tiles = TILES
        self.index = 0
        self.active = None  # the open tile, or None on the carousel
        self.selected = None  # path of the .uf2/.bin chosen for Flash
        self._slide = 0.0  # 1 -> 0 while a tile slides in
        self._slide_dir = 1
        self._toast = ""
        self._toast_ms = 0
        self._tdlg = None
        self._tdlg_done = None
        self._act = 0

    # --- shared services for the tiles ---

    def tile(self, key):
        for t in self.tiles:
            if t.key == key:
                return t

    def attempt(self, fn, ok=None):
        """Run an SWD action; a failure goes to the pop-up line instead of crashing the app."""
        try:
            fn()
        except Exception as e:
            self.note(str(e)[:40] or type(e).__name__)
            return False
        if ok:
            self.note(ok)
        return True

    def note(self, msg):
        self._toast, self._toast_ms = msg, TOAST_MS

    def open_picker(self, return_key):
        files = self.tile("files")
        files.pick_from = return_key
        self._open(files)

    def close_picker(self):
        files = self.tile("files")
        back, files.pick_from = files.pick_from, None
        if back:
            self._open(self.tile(back))

    def ask_text(self, prompt, initial, on_done):
        """Firmware text entry: badge buttons pick letters, a USB keyboard types
        straight in. on_done(text) is called on Enter / the dialog's own OK."""
        dlg = TextDialog(prompt, self, on_complete=self._text_done, on_cancel=self._text_close)
        dlg.text = initial
        self._tdlg, self._tdlg_done = dlg, on_done
        self.overlays.append(dlg)
        eventbus.on(ButtonDownEvent, self._text_extra_keys, self)

    def _text_close(self):
        if self._tdlg is not None:
            if self._tdlg in self.overlays:
                self.overlays.remove(self._tdlg)
            eventbus.remove(ButtonDownEvent, self._text_extra_keys, self)
            self._tdlg = None
        self.buttons.clear()

    def _text_done(self):
        text, done = (self._tdlg.text if self._tdlg else ""), self._tdlg_done
        self._text_close()
        if done:
            done(text)

    def _text_extra_keys(self, event):
        """Enter and Escape on a USB keyboard: the firmware dialog doesn't act on those."""
        dlg = self._tdlg
        if dlg is None or dlg._result is not None or "ENTER" not in self.keyboard:
            return
        if self.keyboard["ENTER"] in event.button and event.button.find_parent_in_group("Keyboard"):
            dlg._cleanup()
            dlg._result = dlg.text
            self._text_done()
        elif self.keyboard["ESCAPE"] in event.button and event.button.find_parent_in_group("Keyboard"):
            dlg._cleanup()
            dlg._result = False
            self._text_close()

    # --- navigation ---

    def _open(self, tile):
        self.buttons.clear()
        self.index = self.tiles.index(tile)
        self.active = tile
        tile.enter(self)

    def _go(self, d):
        self.index = (self.index + d) % len(self.tiles)
        self._slide, self._slide_dir = 1.0, d

    def update(self, delta):
        if self._toast_ms > 0:
            self._toast_ms -= delta
        if self._slide > 0:
            self._slide = max(0.0, self._slide - delta / SLIDE_MS)
        link = self.link
        if link.swd is not None:  # SWD activity LED, written outside the bit loop
            if link.swd.activity != self._act:
                self._act = link.swd.activity
                link.leds.pulse(LED_SWD)
            link.leds.service()
        if self._tdlg is not None:  # the dialog owns the buttons
            return
        b = self.buttons
        if self.active is None:
            if b.pressed(BUTTON_TYPES["RIGHT"]):
                self._go(1)
            elif b.pressed(BUTTON_TYPES["LEFT"]):
                self._go(-1)
            elif b.pressed(BUTTON_TYPES["CONFIRM"]):
                self._open(self.tiles[self.index])
            elif b.pressed(BUTTON_TYPES["CANCEL"]):
                b.clear()
                self.minimise()
            return
        if b.pressed(BUTTON_TYPES["CANCEL"]):
            b.clear()
            tile = self.active
            if not tile.back(self) and self.active is tile:
                self.active = None
            return
        self.active.update(self, b, delta)

    # --- drawing ---

    def draw(self, ctx):
        ctx.save()
        clear_background(ctx)
        if not getattr(self.active, "hide_header", False):
            ui.header(ctx, self.link)
        if self.active is None:
            self._draw_carousel(ctx)
        else:
            self.active.draw(self, ctx)
        if self._toast_ms > 0:
            ui.toast(ctx, self._toast)
        ctx.restore()
        self.draw_overlays(ctx)

    def _draw_carousel(self, ctx):
        t = self.tiles[self.index]
        ctx.save()
        ctx.translate(self._slide * self._slide_dir * 150, 0)
        ui.icon(ctx, t.key, 0, -22, 44, t.colour)
        ui.text(ctx, t.title, 0, 40, ui.TEXT, ui.label_font_size * 1.15)
        ui.text(ctx, t.summary(self), 0, 66, ui.MUTED)
        ctx.restore()
        ui.chevron(ctx, -100, -10, 1)
        ui.chevron(ctx, 100, -10, -1)
        ui.dots(ctx, len(self.tiles), self.index, 90, t.colour)


__app_export__ = SWDApp
