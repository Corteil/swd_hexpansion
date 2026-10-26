"""Program the RP2040's flash over SWD by calling its bootrom flash functions.

Same method as flash_rp2350 (halt the core at reset, run connect_internal_flash,
flash_exit_xip, flash_range_erase / flash_range_program from a RAM buffer, then flush_cache
and enter_cmd_xip), so the stages, progress reporting and verify are shared. The RP2040
differs in three ways: the bootrom function table is found through 16-bit pointers at 0x14
and 0x18, there is no RCP or ACCESSCTRL to set up, and the core is selected by TARGETSEL on
every reconnect (a plain line reset would select the rescue DP, see swd_rp2350.rescue_rp2040).

The flash size is not probed: targets.py says 2 MB, which is what a Pico has.
"""
from . import uf2
from .flash_rp2350 import Flasher as _Flasher, STACK_TOP, _code, _fail

TARGETSEL_CORE0 = 0x01002927
FAMILY_RP2040 = 0xE48BFF56


class Flasher(_Flasher):
    def _lookup(self):
        swd = self.swd
        table = swd.read32(0x14) & 0xFFFF       # bootrom function table
        lookup = swd.read32(0x18) & 0xFFFF      # rom_func_lookup(table, code)
        if not 0x20 <= table < 0x4000 or not 0x10 <= lookup < 0x4000:
            _fail("Flash routines not found", "no bootrom function table")
        for name, code in (("connect", _code("I", "F")), ("exit_xip", _code("E", "X")),
                           ("erase", _code("R", "E")), ("program", _code("R", "P")),
                           ("flush", _code("F", "C")), ("enter_xip", _code("C", "X"))):
            addr = swd.call(lookup, (table, code), sp=STACK_TOP)
            if not addr:
                _fail("Flash routines not found", "bootrom has no %s function" % name)
            self.fn[name] = addr

    def prepare(self):
        """Reset with the core caught at its first instruction, then get the flash chip into a
        state where it can be erased and programmed."""
        swd = self.swd
        swd.reset_halt(TARGETSEL_CORE0)
        self._lookup()
        swd.call(self.fn["connect"], sp=STACK_TOP)
        swd.call(self.fn["exit_xip"], sp=STACK_TOP)

    def run(self, path):
        info = uf2.check(path)
        if info["family"] not in (None, FAMILY_RP2040):
            _fail("File is for another chip", "UF2 family %08X" % info["family"])
        yield from _Flasher.run(self, path)
