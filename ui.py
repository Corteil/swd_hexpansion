"""Drawing helpers: palette, vector icons, progress ring and bar, text.

Everything draws with the origin at the centre of the 240x240 round display.
"""
import math

try:
    from app_components.tokens import small_font_size, label_font_size
except ImportError:  # off the badge (tests)
    small_font_size, label_font_size = 13.5, 19.0

TAU = 2 * math.pi
TEXT = (1, 1, 1)
MUTED = (0.62, 0.62, 0.68)
DIM = (0.2, 0.2, 0.26)
OK = (0.2, 0.9, 0.4)
WARN = (1.0, 0.7, 0.1)
BAD = (1.0, 0.3, 0.3)


def clip(s, n):
    return s if len(s) <= n else s[:n - 2] + ".."


def text(ctx, s, x, y, rgb=TEXT, size=None, align="c"):
    ctx.font_size = size or small_font_size
    ctx.text_align = ctx.LEFT if align == "l" else ctx.CENTER
    ctx.text_baseline = ctx.MIDDLE
    ctx.rgb(*rgb).move_to(x, y).text(s)


def bar(ctx, x, y, w, h, frac, rgb):
    """Horizontal progress bar with its left edge at x."""
    frac = max(0.0, min(1.0, frac))
    ctx.rgb(*DIM).rectangle(x, y, w, h).fill()
    if frac > 0:
        ctx.rgb(*rgb).rectangle(x, y, w * frac, h).fill()


def ring(ctx, frac, rgb, r=112, width=8):
    """Progress ring round the edge of the display, starting at the top."""
    frac = max(0.0, min(1.0, frac))
    ctx.line_width = width
    ctx.rgb(*DIM).arc(0, 0, r, 0, TAU, 0).stroke()
    if frac > 0:
        ctx.rgb(*rgb).arc(0, 0, r, -TAU / 4, -TAU / 4 + TAU * frac, 0).stroke()


def dots(ctx, n, i, y, rgb):
    gap = 14
    x0 = -gap * (n - 1) / 2
    for k in range(n):
        ctx.rgb(*(rgb if k == i else DIM)).arc(x0 + k * gap, y, 4 if k == i else 2.5, 0, TAU, 0).fill()


def chevron(ctx, x, y, direction, rgb=MUTED):
    ctx.line_width = 3
    ctx.rgb(*rgb).move_to(x - 5 * direction, y - 9).line_to(x + 5 * direction, y).line_to(x - 5 * direction, y + 9).stroke()


def header(ctx, link):
    text(ctx, clip(link.label(), 22), 0, -104, link.colour())


# --- icons ---------------------------------------------------------------
# icon(ctx, name, cx, cy, s, rgb): s is the half-size in pixels.

def _line(ctx, x0, y0, x1, y1):
    ctx.move_to(x0, y0).line_to(x1, y1).stroke()


def _plug(ctx, cx, cy, s):
    ctx.rectangle(cx - s, cy - 0.45 * s, 0.9 * s, 0.9 * s).stroke()
    ctx.rectangle(cx + 0.1 * s, cy - 0.45 * s, 0.9 * s, 0.9 * s).stroke()
    _line(ctx, cx - 0.1 * s, cy - 0.2 * s, cx + 0.1 * s, cy - 0.2 * s)
    _line(ctx, cx - 0.1 * s, cy + 0.2 * s, cx + 0.1 * s, cy + 0.2 * s)


def _flash(ctx, cx, cy, s):
    _line(ctx, cx, cy - s, cx, cy - 0.1 * s)
    ctx.move_to(cx - 0.35 * s, cy - 0.4 * s).line_to(cx, cy - 0.05 * s).line_to(cx + 0.35 * s, cy - 0.4 * s).stroke()
    ctx.rectangle(cx - 0.8 * s, cy + 0.15 * s, 1.6 * s, 0.8 * s).stroke()
    for k in (-0.4, 0, 0.4):
        _line(ctx, cx + k * s, cy + 0.95 * s, cx + k * s, cy + 1.15 * s)


def _folder(ctx, cx, cy, s):
    ctx.rectangle(cx - s, cy - 0.7 * s, 0.7 * s, 0.25 * s).stroke()
    ctx.rectangle(cx - s, cy - 0.45 * s, 2 * s, 1.2 * s).stroke()


def _memory(ctx, cx, cy, s):
    c = 0.5 * s
    for i in (-1, 0, 1):
        for j in (-1, 0, 1):
            ctx.rectangle(cx + i * 0.7 * s - c / 2, cy + j * 0.7 * s - c / 2, c, c).fill()


def _core(ctx, cx, cy, s):
    ctx.move_to(cx - s, cy - 0.7 * s).line_to(cx - s, cy + 0.7 * s).line_to(cx - 0.1 * s, cy).close_path().fill()
    ctx.rectangle(cx + 0.3 * s, cy - 0.7 * s, 0.25 * s, 1.4 * s).fill()
    ctx.rectangle(cx + 0.75 * s, cy - 0.7 * s, 0.25 * s, 1.4 * s).fill()


def _serial(ctx, cx, cy, s):
    ctx.rectangle(cx - s, cy - 0.75 * s, 2 * s, 1.5 * s).stroke()
    ctx.move_to(cx - 0.6 * s, cy - 0.3 * s).line_to(cx - 0.2 * s, cy).line_to(cx - 0.6 * s, cy + 0.3 * s).stroke()
    _line(ctx, cx, cy + 0.35 * s, cx + 0.5 * s, cy + 0.35 * s)


def _settings(ctx, cx, cy, s):
    ctx.arc(cx, cy, 0.5 * s, 0, TAU, 0).stroke()
    for k in range(8):
        a = k * TAU / 8
        _line(ctx, cx + 0.7 * s * math.cos(a), cy + 0.7 * s * math.sin(a),
              cx + 1.0 * s * math.cos(a), cy + 1.0 * s * math.sin(a))


ICONS = {
    "connect": _plug, "flash": _flash, "files": _folder, "memory": _memory,
    "core": _core, "serial": _serial, "settings": _settings,
}


def icon(ctx, name, cx, cy, s, rgb):
    ctx.line_width = max(2, s / 9)
    ctx.rgb(*rgb)
    ICONS[name](ctx, cx, cy, s)
