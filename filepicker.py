"""File explorer for choosing the .uf2 / .bin to flash.

FilePicker is the pure logic (listing, sorting, UF2 header sniffing) and has no
badge imports, so it can be tested off the badge. FilesTile is the tile that
wraps it: used on its own it browses and marks a file as selected, and opened
from the Flash tile it returns to Flash as soon as a file is chosen.
"""
import os
import struct

from . import ui
from .tile import Tile

try:
    import vfs
except ImportError:  # off the badge
    vfs = None

EXTS = (".uf2", ".bin")
ROWS = 5
MIN_CARD_BYTES = 256 << 10  # smaller mounts (hexpansion EEPROMs, ~8 KB) aren't storage
UF2_MAGIC0, UF2_MAGIC1 = 0x0A324655, 0x9E5D5157
FAMILIES = {
    0xE48BFF56: "RP2040", 0xE48BFF57: "absolute", 0xE48BFF58: "data",
    0xE48BFF59: "RP2350 ARM-S", 0xE48BFF5A: "RP2350 RISC-V", 0xE48BFF5B: "RP2350 ARM-NS",
}
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
                head = f.read(32)
            if len(head) < 32:
                return ["%s" % human(size), "too short for UF2"]
            m0, m1, _flags, addr, _n, _blk, blocks, fam = struct.unpack("<8I", head)
            if m0 != UF2_MAGIC0 or m1 != UF2_MAGIC1:
                return ["%s" % human(size), "not a UF2 file"]
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

    def summary(self, app):
        return ui.clip(app.selected.rsplit("/", 1)[-1], 24) if app.selected else self.hint

    def enter(self, app):
        self.picker.show_storages()

    def back(self, app):
        if self.picker.back():
            return True
        if self.pick_from:
            app.close_picker()
            return True
        return False

    def update(self, app, b, delta):
        from events.input import BUTTON_TYPES
        p = self.picker
        if b.pressed(BUTTON_TYPES["DOWN"]):
            p.move(1)
        elif b.pressed(BUTTON_TYPES["UP"]):
            p.move(-1)
        elif b.pressed(BUTTON_TYPES["CONFIRM"]):
            path = p.activate()
            if path:
                app.selected = path
                if self.pick_from:
                    app.close_picker()
                else:
                    app.note("Selected for Flash")
        else:
            for ch in "abcdefghijklmnopqrstuvwxyz":
                key = app.keyboard.get(ch.upper())
                if key is not None and b.pressed(key):
                    p.jump(ch)
                    break

    def draw(self, app, ctx):
        p = self.picker
        ui.icon(ctx, "files", -62, -74, 10, self.colour)
        where = "storage" if p.path is None else p.path
        ui.text(ctx, ui.clip(where[-18:], 18), 8, -74, ui.MUTED, align="c")
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
            ui.text(ctx, ui.clip(label + ("/" if is_dir else ""), 20) + (" *" if mark == "*" else ""), -84, y, colour, align="l")
        cur = p.current()
        if cur and not cur[2]:
            for k, line in enumerate(describe(cur[1])):
                ui.text(ctx, ui.clip(line, 26), 0, 62 + k * 16, ui.MUTED)
        elif p.entries:
            ui.text(ctx, "%d / %d" % (p.cur + 1, len(p.entries)), 0, 66, ui.MUTED)
