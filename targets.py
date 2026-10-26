"""Target profiles: what is different between chips that all speak SWD.

The debug core (swd_rp2350.SWD) is the same for every Cortex-M; a profile says how to
select the chip, where its AP is, and where flash and RAM live. Adding a chip is a new
Target line here, plus a flash algorithm once flashing is wired in (algo=None means the
chip can be read, halted and reset but not programmed).

Values marked "verify" come from memory of the datasheets and have not been checked
against a real chip or an OpenOCD trace yet. The RP2350 line was checked on a Pico 2, the
RP2040 line (connect, halt, reset, memory reads, matching a flash backup) on a Pico.
"""


class Target:
    def __init__(self, name, targetsel, adiv, ap_base, flash, flash_size, ram, dpidr=None, algo=None):
        self.name = name
        self.targetsel = targetsel  # multidrop TARGETSEL, or None for a single-drop target
        self.adiv = adiv            # 5 or 6
        self.ap_base = ap_base      # ADIv6: AP address; ADIv5: APSEL << 24
        self.flash = flash
        self.flash_size = flash_size
        self.ram = ram
        self.dpidr = dpidr          # expected DPIDR, used to recognise the chip (verify)
        self.algo = algo            # flash algorithm (RAM stub), not written yet


# Order matters for auto-detect: RP2040 first. An awake RP2040 answers a plain line reset (no
# TARGETSEL) as its rescue DP, and once that has been selected the core DPs stop answering until a
# rescue reset. The RP2040's core TARGETSEL just gets no reply from an RP2350, which is harmless.
TARGETS = [
    # Pico: needs the dormant wake-up, then TARGETSEL core 0; 2 MB flash (not probed; the XIP window is 16 MB)
    Target("RP2040", 0x01002927, 5, 0x00000000, 0x10000000, 2 << 20, 0x20000000, dpidr=0x0BC12477),
    # DPIDR and single-drop connect confirmed on a Pico 2 against OpenOCD + Debug Probe
    Target("RP2350", None, 6, 0x2000, 0x10000000, 4 << 20, 0x20000000, dpidr=0x4C013477),
    Target("Cortex-M", None, 5, 0x00000000, 0x08000000, 0, 0x20000000),  # generic: nothing known about flash
]


def by_name(name):
    for t in TARGETS:
        if t.name == name:
            return t
