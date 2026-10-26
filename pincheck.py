"""Settings -> Pin check: is the target wired the way the app expects?

run(port) is a generator that yields the list of result lines after each step, so the UI can
show them as they arrive: (text, ok) with ok True / False / None (information). Steps:
  1 SWD: try SWCLK/SWDIO on every ordered pair of the four signal pins and say where the target
    answers (normal, exchanged, or elsewhere), which chip it is, or that nothing answers.
  2 UART: watch the two UART pins for traffic and say which one carries the target's TX.
  3 LEDs: light the three activity LEDs in turn.
Read-only apart from the LEDs: SWD only does line resets and debug-port reads.
"""
import time

NAMES = "FGHI"   # hexpansion signal pins HS_F..HS_I
CHIPS = {0x0BC12477: "RP2040", 0x4C013477: "RP2350", 0x10212927: "RP2040 rescue DP"}
RESCUE = 0x10212927
WATCH_MS = 1500


def _pairs():
    """Ordered (clock, data) pin indexes, the app's own wiring (HS_F, HS_G) first."""
    first = [(0, 1)]
    rest = [(c, d) for c in range(4) for d in range(4) if c != d and (c, d) != (0, 1)]
    return first + rest


def classify(found, chip):
    """Result line for the SWD step. found is (clock_index, data_index) where the target answered,
    or None."""
    if found is None:
        return ("SWD: no reply on any pin pair", False)
    c, d = found
    where = "HS_%s/HS_%s" % (NAMES[c], NAMES[d])
    if (c, d) == (0, 1):
        return ("SWD ok  %s  %s" % (where, chip), True)
    if (c, d) == (1, 0):
        return ("SWD: clock and data swapped", False)
    return ("SWD answers on %s" % where, False)


def uart_line(edges):
    """Result line for the UART step. edges is the transition count seen on (HS_H, HS_I)."""
    h, i = edges
    if h and i:
        return ("UART: both pins busy", None)
    if i:
        return ("UART: target TX on HS_I (default)", True)
    if h:
        return ("UART: target TX on HS_H, swap in Serial", True)
    return ("UART: silent (nothing sent)", None)


def _scan_swd(port):
    from system.hexpansion.config import HexpansionConfig, _pin_mapping
    from .swd_rp2350 import SWD, SWDError
    cfg = HexpansionConfig(port)
    hs = _pin_mapping[port]["hs"]
    for c, d in _pairs():
        swd = SWD(cfg.pin[c], cfg.pin[d], 0, gpio=(hs[c], hs[d]))
        try:
            swd.enable()
            # RP2040 core 0 first: a plain line reset on an awake RP2040 selects its rescue DP and
            # silences the cores. The plain attempt (RP2350) comes only after those got no reply.
            for ts, dormant in ((0x01002927, False), (0x01002927, True), (None, False), (None, True)):
                try:
                    dpidr = swd._try_select(ts, dormant)
                except SWDError:
                    continue
                return (c, d), dpidr
        finally:
            swd.disable()
    return None, None


def _watch_uart(port):
    from machine import Pin
    from system.hexpansion.config import HexpansionConfig
    cfg = HexpansionConfig(port)
    pins = (cfg.pin[2], cfg.pin[3])
    for p in pins:
        p.init(Pin.IN)
    edges = [0, 0]
    last = [p.value() for p in pins]
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < WATCH_MS:
        for k in (0, 1):
            v = pins[k].value()
            if v != last[k]:
                edges[k] += 1
                last[k] = v
    return edges


def run(port):
    lines = []
    yield list(lines) + [("SWD: looking...", None)]
    found, dpidr = _scan_swd(port)
    if found is not None and dpidr == RESCUE:
        lines.append(("SWD: RP2040 rescue DP only", False))
        lines.append(("use Settings > Rescue DP", None))
    else:
        lines.append(classify(found, CHIPS.get(dpidr, "%08X" % (dpidr or 0))))
    yield list(lines) + [("UART: listening...", None)]
    lines.append(uart_line(_watch_uart(port)))
    yield list(lines) + [("LEDs: green blue white", None)]
    from system.hexpansion.config import HexpansionConfig
    from .leds import Leds
    leds = Leds(HexpansionConfig(port).ls_pin)
    try:
        for led in (0, 1, 2):
            leds.pulse(led, 350)
            t0 = time.ticks_ms()
            while time.ticks_diff(time.ticks_ms(), t0) < 450:
                leds.service()
                time.sleep_ms(20)
    finally:
        leds.off()
    lines.append(("LEDs: green, blue, white shown", None))
    yield list(lines)
