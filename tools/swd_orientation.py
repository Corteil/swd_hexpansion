"""Run on the badge (mpremote run) to see whether a target answers SWD when SWCLK and SWDIO
are on any two of the four hexpansion signal pins HS_F..HS_I, in either order. Read-only: it
only does line resets and DPIDR reads. The pair HS_F/HS_G is the app's normal wiring."""
import system.scheduler
import sys
sys.path.insert(0, '/apps')
from swd_programmer.swd_rp2350 import SWD
from system.hexpansion.config import HexpansionConfig, _pin_mapping

PORT = 2
cfg = HexpansionConfig(PORT)
hs = _pin_mapping[PORT]["hs"]
NAMES = "FGHI"
found = []
for c in range(4):
    for d in range(4):
        if c == d:
            continue
        s = SWD(cfg.pin[c], cfg.pin[d], 0, gpio=(hs[c], hs[d]))
        s.enable()
        for dormant in (False, True):
            for ts in (None, 0x01002927):   # plain (RP2350, awake RP2040) and RP2040 core 0
                try:
                    v = s._try_select(ts, dormant)
                    found.append((c, d, dormant, ts, v))
                    print("ANSWER: SWCLK=HS_%s SWDIO=HS_%s dormant=%s targetsel=%s DPIDR %08X" % (NAMES[c], NAMES[d], dormant, ts and "%08X" % ts, v))
                except Exception:
                    pass
        s.disable()
print("pairs tried: 12; answers:", len(found))
