"""File explorer for choosing the .uf2 / .bin to flash.

FilePicker is the pure logic (listing, sorting, UF2 header sniffing) and has no
badge imports, so it can be tested off the badge. FilesTile is the tile that
wraps it: used on its own it browses and marks a file as selected, and opened
from the Flash tile it returns to Flash as soon as a file is chosen.
"""
import os
import struct

from . import ui, uf2
from .tile import Tile, flasher_for

try:
    import vfs
except ImportError:  # off the badge
    vfs = None

EXTS = (".uf2", ".bin")
ROWS = 5
MIN_CARD_BYTES = 256 << 10  # smaller mounts (hexpansion EEPROMs, ~8 KB) aren't storage
UF2_MAGIC0, UF2_MAGIC1 = 0x0A324655, 0x9E5D5157
FAMILIES = uf2.FAMILY_NAMES
S_IFDIR = 0x4000


def _listdir(path):
    """[(name, is_dir, size)] for a directory."""
    if hasattr(os, "ilistdir"):
        return [(e[0], bool(e[1] & S_IFDIR), e[3] if len(e) > 3 else 0) for e in os.ilistdir(path)]
    out = []
    for name in os.listdir(path):
        st = os.stat(path.rstrip("/") + "/" + name)
        out.append((name, bool(st[0] & S_IFDIR), st[6]))
    return out


def human(n):
    if n >= 1 << 20:
        return "%.1f MB" % (n / (1 << 20))
    if n >= 1 << 10:
        return "%.1f KB" % (n / (1 << 10))
    return "%d B" % n


def describe(path):
    """Up to two short lines about a .uf2 or .bin file."""
    try:
        size = os.stat(path)[6]
        if path.lower().endswith(".uf2"):
            with open(path, "rb") as f:
                for _ in range(3):   # the SDK puts an "absolute" family block first: skip it
                    head = f.read(32)
                    f.seek(f.tell() + 480)
                    if len(head) < 32:
                        return ["%s" % human(size), "too short for UF2"]
                    m0, m1, _flags, addr, _n, _blk, blocks, fam = struct.unpack("<8I", head)
                    if m0 != UF2_MAGIC0 or m1 != UF2_MAGIC1:
                        return ["%s" % human(size), "not a UF2 file"]
                    if fam != 0xE48BFF57:
                        break
            where = "" if addr == 0x10000000 else " @ %X" % addr
            return ["%s  %d blocks" % (human(size), blocks),
                    FAMILIES.get(fam, "family %08X" % fam) + where]
        return ["%s" % human(size), "raw binary"]
    except OSError:
        return ["unreadable"]


class FilePicker:
    def __init__(self, exts=EXTS):
        self.exts = exts
        self.path = None  # None = the list of storages
        self.entries = []  # (label, full_path, is_dir, size)
        self.cur = 0
        self.top = 0
        self.roots = []
        self.error = ""
        self.show_storages()

    # --- storages and listing ---

    def _cards(self):
        names = []
        if vfs is None:
            return names
        for fs, path in vfs.mount():
            if path == "/" or path.count("/") != 1:
                continue
            try:
                v = os.statvfs(path)
            except OSError:
                continue
            if v[1] * v[2] >= MIN_CARD_BYTES:
                names.append(path)
        return sorted(names)

    def show_storages(self):
        self.path = None
        self.roots = ["/"] + self._cards()
        self.entries = [("flash" if p == "/" else p[1:], p, True, 0) for p in self.roots]
        self.cur = self.top = 0
        self.error = ""

    def open(self, path):
        try:
            raw = _listdir(path)
        except OSError:
            self.error = "cannot read"
            return
        self.error = ""
        keep = [e for e in raw if e[1] or e[0].lower().endswith(self.exts)]
        keep.sort(key=lambda e: (not e[1], e[0].lower()))
        base = path.rstrip("/")
        self.entries = [(n, base + "/" + n, d, s) for n, d, s in keep]
        self.path = path
        self.cur = self.top = 0

    # --- navigation ---

    def move(self, d):
        if not self.entries:
            return
        self.cur = (self.cur + d) % len(self.entries)
        if self.cur < self.top:
            self.top = self.cur
        elif self.cur >= self.top + ROWS:
            self.top = self.cur - ROWS + 1
        if d > 0 and self.cur == 0:
            self.top = 0

    def jump(self, ch):
        """Move to the next entry starting with ch (USB keyboard type-ahead)."""
        n = len(self.entries)
        for k in range(1, n + 1):
            i = (self.cur + k) % n
            if self.entries[i][0].lower().startswith(ch):
                self.move(i - self.cur)
                return True
        return False

    def current(self):
        return self.entries[self.cur] if self.entries else None

    def activate(self):
        """Enter a directory, or return the full path of the chosen file."""
        e = self.current()
        if e is None:
            return None
        if e[2]:
            self.open(e[1])
            return None
        return e[1]

    def back(self):
        """Up one directory. False when already at the list of storages."""
        if self.path is None:
            return False
        if self.path in self.roots:
            self.show_storages()
            return True
        p = self.path.rstrip("/")
        self.open(p[:p.rfind("/")] or "/")
        return True


class FilesTile(Tile):
    key = "files"
    title = "Files"
    colour = (1.0, 0.8, 0.2)
    hint = "pick .uf2 / .bin"

    def __init__(self):
        self.picker = FilePicker()
        self.pick_from = None  # key of the tile to return to once a file is chosen
        self.pending = None    # a file waiting for its second OK
        self.verdict = None    # (ok, text) from checking it against the connected chip, or None

    def summary(self, app):
        return ui.clip(app.selected.rsplit("/", 1)[-1], 24) if app.selected else self.hint

    def enter(self, app):
        self.pending = None
        self.picker.show_storages()

    def _judge(self, app, path):
        """Does this file suit the connected chip? Worked out once, when the confirm screen opens, not
        every frame. None when no RP2040 or RP2350 is connected to check it against."""
        flasher = flasher_for(app.link)
        if flasher is None:
            return None
        try:
            flasher.check(path)
        except Exception as e:   # UF2Error or FlashError: the message is meant for the screen
            return (False, str(e) or type(e).__name__)
        return (True, "Suits %s" % flasher.CHIP)

    def back(self, app):
        if self.pending:   # CANCEL on the confirm screen: back to the list, nothing chosen
            self.pending, self.verdict = None, None
            return True
        if self.picker.back():
            return True
        if self.pick_from:
            app.close_picker()
            return True
        return False

    def update(self, app, b, delta):
        from events.input import BUTTON_TYPES
        p = self.picker
        if self.pending:   # the confirm screen: only OK (or CANCEL, see back) does anything
            if b.pressed(BUTTON_TYPES["CONFIRM"]):
                app.selected, self.pending, self.verdict = self.pending, None, None
                if self.pick_from:
                    app.close_picker()
                else:
                    app.note("File ready to flash")
            return
        if b.pressed(BUTTON_TYPES["DOWN"]):
            p.move(1)
        elif b.pressed(BUTTON_TYPES["UP"]):
            p.move(-1)
        elif b.pressed(BUTTON_TYPES["CONFIRM"]):
            self.pending = p.activate()   # a file asks for confirmation; a folder just opens
            if self.pending:
                self.verdict = self._judge(app, self.pending)
        else:
            for ch in "abcdefghijklmnopqrstuvwxyz":
                key = app.keyboard.get(ch.upper())
                if key is not None and b.pressed(key):
                    p.jump(ch)
                    break

    def _draw_confirm(self, ctx):
        ui.icon(ctx, "files", 0, -68, 12, self.colour)
        ui.text(ctx, "Use this file?", 0, -42, ui.TEXT, ui.label_font_size)
        ui.text(ctx, self.pending.rsplit("/", 1)[-1], 0, -18, self.colour, tail=7)
        for k, line in enumerate(describe(self.pending)):
            ui.text(ctx, ui.clip(line, 26), 0, 2 + k * 17, ui.MUTED)
        if self.verdict is None:
            ui.text(ctx, "connect to check it", 0, 40, ui.MUTED)
        else:   # green when the connected chip can take it, amber with the reason when it cannot
            ok, text = self.verdict
            ui.text(ctx, ui.clip(text, 28), 0, 40, ui.OK if ok else ui.WARN)
        ui.text(ctx, "OK to confirm", 0, 58, ui.TEXT)
        ui.text(ctx, "Cancel to go back", 0, 75, ui.MUTED)

    def draw(self, app, ctx):
        if self.pending:
            self._draw_confirm(ctx)
            return
        p = self.picker
        ui.icon(ctx, "files", -62, -74, 10, self.colour)
        where = "storage" if p.path is None else p.path
        # left-aligned beside the icon, shortened at the front so the folder you are in stays visible
        ui.text(ctx, where, -48, -74, ui.MUTED, align="l", tail=9)
        if not p.entries:
            ui.text(ctx, p.error or "no .uf2 or .bin here", 0, -10, ui.MUTED)
        for r in range(ROWS):
            i = p.top + r
            if i >= len(p.entries):
                break
            label, full, is_dir, _size = p.entries[i]
            y = -46 + r * 22
            if i == p.cur:
                ctx.rgb(*ui.DIM).rectangle(-92, y - 10, 184, 20).fill()
            mark = "/" if is_dir else ("*" if full == app.selected else " ")
            colour = self.colour if is_dir else ui.TEXT
            ui.text(ctx, ("* " if mark == "*" else "") + label + ("/" if is_dir else ""), -84, y, colour, align="l", tail=0 if is_dir else 7)
        cur = p.current()
        if cur and not cur[2]:
            for k, line in enumerate(describe(cur[1])):
                ui.text(ctx, ui.clip(line, 26), 0, 62 + k * 16, ui.MUTED)
        elif p.entries:
            ui.text(ctx, "%d / %d" % (p.cur + 1, len(p.entries)), 0, 66, ui.MUTED)
