"""The three activity LEDs on the proto hexpansion: LS_A green = serial TX,
LS_B blue = serial RX, LS_C white = SWD activity.

They are wired from 3V3 to the expander pin, so a pin driven LOW lights the LED
(active low; confirmed on the bench: driving 0 lit all three). They sit behind the
badge's I2C port expander, so writes are slow. Nothing here
is called from the SWD bit loop: callers pulse() on an event and the app calls
service() once per frame, which only writes a pin when its state changes.
"""
import time

TX, RX, SWD = 0, 1, 2


class Leds:
    def __init__(self, ls_pins):
        self.pins = [ls_pins[0], ls_pins[1], ls_pins[2]]
        self.until = [0, 0, 0]
        self.pending = [0, 0, 0]  # pulse ms asked for, not yet lit
        self.state = [0, 0, 0]  # 1 = lit
        for p in self.pins:
            p.init(p.OUT)
            p.value(1)  # off

    def pulse(self, led, ms=80):
        # The on-time starts at the next service(): a frame can take longer than ms,
        # and a pulse that expired before it was ever written would never show.
        self.pending[led] = ms

    def service(self):
        now = time.ticks_ms()
        for i, p in enumerate(self.pins):
            if self.pending[i]:
                self.until[i] = time.ticks_add(now, self.pending[i]) or 1
                self.pending[i] = 0
            want = 1 if self.until[i] and time.ticks_diff(self.until[i], now) > 0 else 0
            if want != self.state[i]:
                p.value(0 if want else 1)  # active low
                self.state[i] = want

    def off(self):
        self.until = [0, 0, 0]
        self.pending = [0, 0, 0]
        self.service()
