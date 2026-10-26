"""Program the RP2040's flash over SWD by calling its bootrom flash functions.

Same method as flash_rp2350 (halt the core at reset, run connect_internal_flash,
flash_exit_xip, flash_range_erase / flash_range_program from a RAM buffer, then flush_cache
and enter_cmd_xip), so the stages, progress reporting and verify are shared. The RP2040
differs in three ways: the bootrom function table is found through 16-bit pointers at 0x14
and 0x18, there is no RCP or ACCESSCTRL to set up, and the core is selected by TARGETSEL on
every reconnect (a plain line reset would select the rescue DP, see swd_rp2350.rescue_rp2040).

The flash size is read from the chip's JEDEC ID (see jedec_id); targets.py's 2 MB, what a Pico
has, is used when the ID is not one that encodes a capacity.
"""
from . import uf2
from .flash_rp2350 import Flasher as _Flasher, STACK_TOP, _code, _fail

TARGETSEL_CORE0 = 0x01002927
FAMILY_RP2040 = 0xE48BFF56
IO_QSPI_SS_CTRL = 0x4001800C   # GPIO_QSPI_SS_CTRL, OUTOVER in bits 9:8
SSI_SR, SSI_DR0 = 0x18000028, 0x18000060
SR_RFNE = 1 << 3
MAKERS = {0xEF: "Winbond", 0xC8: "GigaDevice", 0xC2: "Macronix", 0x9D: "ISSI", 0x20: "XMC/Micron",
          0x85: "Puya", 0x0B: "XTX", 0x5E: "Zbit", 0x1C: "Eon", 0x68: "Boya", 0xA1: "Fudan"}


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

    def _spi(self, tx, n_rx):
        """Send tx bytes on the flash's SPI port and return n_rx bytes read after them. Only valid
        between connect_internal_flash/exit_xip and enter_cmd_xip, like the SDK's flash_do_cmd:
        the bootrom leaves the SSI as a plain 8-bit SPI port and we hold chip select ourselves."""
        swd = self.swd
        ctrl = swd.read32(IO_QSPI_SS_CTRL)
        swd.write32(IO_QSPI_SS_CTRL, (ctrl & ~0x300) | (2 << 8))   # drive CS low
        try:
            out = []
            for i in range(len(tx) + n_rx):
                swd.write32(SSI_DR0, tx[i] if i < len(tx) else 0)
                for _ in range(100):
                    if swd.read32(SSI_SR) & SR_RFNE:
                        break
                else:
                    _fail("Flash chip did not answer", "SSI receive FIFO stayed empty")
                b = swd.read32(SSI_DR0) & 0xFF
                if i >= len(tx):
                    out.append(b)
            return out
        finally:
            swd.write32(IO_QSPI_SS_CTRL, ctrl)

    def jedec_id(self):
        """(manufacturer, type, capacity code) from command 0x9F, once prepare() has run."""
        return tuple(self._spi((0x9F,), 3))

    @staticmethod
    def size_from_id(jedec):
        """Capacity in bytes from the ID's last byte (a power-of-two exponent for nearly every
        maker), or None if it does not look like one. Capped at 16 MB: the XIP window and
        the 24-bit addresses of the bootrom flash functions."""
        exp = jedec[2]
        if jedec[0] in (0x00, 0xFF) or not 0x0F <= exp <= 0x1C:
            return None
        return min(1 << exp, 16 << 20)

    def probe(self):
        """(jedec, size or None, description). Resets the chip with the core halted, like a flash."""
        self.prepare()
        try:
            return self.describe()
        finally:
            self.finish()

    def describe(self):
        jedec = self.jedec_id()
        size = self.size_from_id(jedec)
        maker = MAKERS.get(jedec[0], "%02X" % jedec[0])
        text = "%s %s" % (maker, ("%d MB" % (size >> 20)) if size and size >= 1 << 20 else
                          ("%d KB" % (size >> 10)) if size else "size unknown")
        return jedec, size, text

    def prepare(self):
        """Reset with the core caught at its first instruction, then get the flash chip into a
        state where it can be erased and programmed."""
        swd = self.swd
        swd.reset_halt(TARGETSEL_CORE0)
        self._lookup()
        swd.call(self.fn["connect"], sp=STACK_TOP)
        swd.call(self.fn["exit_xip"], sp=STACK_TOP)
        size = self.size_from_id(self.jedec_id())
        if size:
            self.flash_size = size

    def run(self, path):
        info = uf2.check(path)
        if info["family"] not in (None, FAMILY_RP2040):
            _fail("File is for another chip", "UF2 family %08X" % info["family"])
        yield from _Flasher.run(self, path)
