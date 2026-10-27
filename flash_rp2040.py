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
import struct

from . import uf2
from .flash_rp2350 import CRC_BYTES, Flasher as _Flasher, STACK_TOP, _code, _fail

TARGETSEL_CORE0 = 0x01002927
IO_QSPI_SS_CTRL = 0x4001800C   # GPIO_QSPI_SS_CTRL, OUTOVER in bits 9:8
SSI_SR, SSI_DR0 = 0x18000028, 0x18000060
SR_RFNE = 1 << 3


def _crc32(data):
    """The boot loader's checksum: CRC-32, polynomial 0x04C11DB7, seed 0xFFFFFFFF, no bit reversal."""
    crc = 0xFFFFFFFF
    for b in data:
        crc ^= b << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
    return crc


def _covers(have, start, end):
    """True if the (offset, length) pieces in have cover every byte from start to end."""
    pos = start
    for off, n in sorted(have):
        if off > pos:
            break
        pos = max(pos, off + n)
    return pos >= end


class Flasher(_Flasher):
    CHIP = "RP2040"
    FAMILIES_OK = (uf2.FAMILY_RP2040, uf2.FAMILY_DATA)

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

    def prepare(self):
        """Reset with the core caught at its first instruction, then get the flash chip into a
        state where it can be erased and programmed."""
        swd = self.swd
        swd.reset_halt(TARGETSEL_CORE0)
        self._lookup()
        swd.call(self.fn["connect"], sp=STACK_TOP)
        swd.call(self.fn["exit_xip"], sp=STACK_TOP)
        self.detect_size()

    def _check_boot(self, head, have):
        """RP2040: the first 256 bytes are the second-stage boot loader, whose last four bytes are a
        CRC-32 of the other 252 (the bootrom refuses to start an image without it)."""
        if not _covers(have, 0, 256):
            _fail("No RP2040 boot loader")
        if _crc32(head[:CRC_BYTES]) != struct.unpack("<I", head[CRC_BYTES:256])[0]:
            _fail("Bad RP2040 boot loader")
