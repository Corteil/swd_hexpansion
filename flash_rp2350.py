"""Program the RP2350's flash over SWD by calling its bootrom flash functions.

The core is halted and each bootrom function is run on it (SWD.call), the way the Pico SDK
does it from a running program: connect_internal_flash, flash_exit_xip, then
flash_range_erase / flash_range_program from a RAM buffer, then flush_cache and
enter_cmd_xip so the flash can be read back at 0x10000000.

Flasher.run(path) is a generator that yields (stage, fraction) after each unit of work
(a sector window), so the UI can draw progress and stay responsive. Stages: 0 erase,
1 program, 2 verify, 3 done. Raises FlashError on a problem. Closing the generator early
puts the flash back in XIP mode.
"""
import struct
import time

from . import uf2

XIP = 0x10000000
SECTOR = 4096
BLOCK64 = 65536
BUF = 0x20020000     # staging buffer in SRAM, one sector
CODE = 0x20010400    # helper code; the stack grows down from here (as in OpenOCD)
STACK_TOP = 0x20010400
ACCESSCTRL_CFGRESET = 0x40060008
# Thumb code captured from OpenOCD: enable CP7 (RCP) in CPACR, and if the RCP is not yet
# initialised give it a salt (any value will do for a debugger), then SEV and BKPT.
RCP_INIT = (0xF45F4806, 0x60014140, 0xF710EE30, 0xEC40D404, 0xEC400780, 0xBF400781, 0x0000BE00, 0xE000ED88)
RT_ARM_SEC = 0x0004  # bootrom table flag: Arm secure function
ERASE_BLOCK_CMD = 0xD8
ERASE_TIMEOUT_MS = 10000
QMI_CSR, QMI_TX, QMI_RX = 0x400D0000, 0x400D0004, 0x400D0008   # direct mode: control, transmit, receive
CSR_EN, CSR_ASSERT_CS0N, CSR_TXFULL, CSR_RXEMPTY = 1, 1 << 2, 1 << 10, 1 << 16
MAKERS = {0xEF: "Winbond", 0xC8: "GigaDevice", 0xC2: "Macronix", 0x9D: "ISSI", 0x20: "XMC/Micron",
          0x85: "Puya", 0x0B: "XTX", 0x5E: "Zbit", 0x1C: "Eon", 0x68: "Boya", 0xA1: "Fudan"}


class FlashError(Exception):
    pass


def _fail(msg, detail=""):
    """Raise FlashError(msg); msg is shown to the user, detail goes to the USB console."""
    print("flash:", msg, detail)
    raise FlashError(msg)


def _code(a, b):
    return ord(a) | (ord(b) << 8)


def windows(path):
    """Yield (base, bytearray(SECTOR)) for each 4 KB window the image touches, in address
    order, padded with 0xFF. The image must be in ascending order (UF2 from the SDK is)."""
    base = None
    buf = None
    for addr, data in uf2.blocks(path):
        while data:
            wbase = addr & ~(SECTOR - 1)
            if wbase != base:
                if base is not None:
                    if wbase < base:
                        _fail("File is not in address order", "UF2 blocks go backwards")
                    yield base, buf
                base, buf = wbase, bytearray(b"\xff" * SECTOR)
            n = min(len(data), SECTOR - (addr - base))
            buf[addr - base:addr - base + n] = data[:n]
            addr += n
            data = data[n:]
    if base is not None:
        yield base, buf


class Flasher:
    def __init__(self, swd, flash_size=4 << 20):
        self.swd = swd
        self.flash_size = flash_size
        self.fn = {}

    # --- bootrom ---
    def _lookup(self):
        swd = self.swd
        table_lookup = (swd.read32(0x14) >> 16) & 0xFFFF   # 16-bit pointer at 0x16
        if not 0x20 <= table_lookup < 0x8000:
            _fail("Flash routines not found", "no bootrom function table")
        for name, code in (("connect", _code("I", "F")), ("exit_xip", _code("E", "X")),
                           ("erase", _code("R", "E")), ("program", _code("R", "P")),
                           ("flush", _code("F", "C")), ("enter_xip", _code("C", "X")),
                           ("state_reset", _code("S", "R"))):
            addr = swd.call(table_lookup, (code, RT_ARM_SEC), sp=STACK_TOP)
            if not addr:
                _fail("Flash routines not found", "bootrom has no %s function" % name)
            self.fn[name] = addr

    # --- flash chip identification ---
    def _spi(self, tx, n_rx):
        """Send tx bytes to the flash chip and return the n_rx bytes read after them, through the
        QMI's direct mode as the SDK's flash_do_cmd does. Only valid after connect_internal_flash
        and exit_xip, before enter_cmd_xip."""
        swd = self.swd
        csr = swd.read32(QMI_CSR)
        swd.write32(QMI_CSR, csr | CSR_ASSERT_CS0N)
        swd.write32(QMI_CSR, csr | CSR_ASSERT_CS0N | CSR_EN)
        try:
            out = []
            for i in range(len(tx) + n_rx):
                for _ in range(100):
                    if not swd.read32(QMI_CSR) & CSR_TXFULL:
                        break
                swd.write32(QMI_TX, tx[i] if i < len(tx) else 0)
                for _ in range(100):
                    if not swd.read32(QMI_CSR) & CSR_RXEMPTY:
                        break
                else:
                    _fail("Flash chip did not answer", "QMI receive FIFO stayed empty")
                b = swd.read32(QMI_RX) & 0xFF
                if i >= len(tx):
                    out.append(b)
            return out
        finally:
            swd.write32(QMI_CSR, (csr | CSR_ASSERT_CS0N) & ~CSR_EN)
            swd.write32(QMI_CSR, csr & ~(CSR_EN | CSR_ASSERT_CS0N))

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

    def describe(self):
        jedec = self.jedec_id()
        size = self.size_from_id(jedec)
        maker = MAKERS.get(jedec[0], "%02X" % jedec[0])
        text = "%s %s" % (maker, ("%d MB" % (size >> 20)) if size and size >= 1 << 20 else
                          ("%d KB" % (size >> 10)) if size else "size unknown")
        return jedec, size, text

    def detect_size(self):
        """Use the real flash size from here on, if the chip's ID says what it is."""
        size = self.size_from_id(self.jedec_id())
        if size:
            self.flash_size = size

    def probe(self):
        """(jedec, size or None, description). Resets the chip with the core halted, like a flash."""
        self.prepare()
        try:
            return self.describe()
        finally:
            self.finish()

    def prepare(self):
        """Reset the chip with the core caught at its first instruction (Secure state, no
        stale faults or stack limits), then do what OpenOCD's rp2350 driver does before it
        calls any bootrom function: reset ACCESSCTRL, initialise the RCP (the bootrom's
        redundancy coprocessor, which the boot code would normally set up), and clear the
        bootrom's per-core state. Sequence taken from a logic-analyser capture of OpenOCD."""
        swd = self.swd
        swd.release_core1()
        swd.reset_halt()
        swd.write32(ACCESSCTRL_CFGRESET, 0xACCE0001)
        swd.write_block(CODE, RCP_INIT)
        swd.write_reg(0x14, 1)                      # PRIMASK = 1
        swd.call(CODE, sp=STACK_TOP)
        self._lookup()
        swd.call(self.fn["state_reset"], (1,), sp=STACK_TOP)   # bootrom_state_reset(current core)
        swd.call(self.fn["connect"], sp=STACK_TOP)
        swd.call(self.fn["exit_xip"], sp=STACK_TOP)
        self.detect_size()

    def finish(self):
        """Flush the cache and put the flash back in XIP mode (readable at 0x10000000)."""
        self.swd.call(self.fn["flush"], sp=STACK_TOP)
        self.swd.call(self.fn["enter_xip"], sp=STACK_TOP)

    # --- the three stages ---
    def erase(self, start, end):
        off = (start - XIP) & ~(SECTOR - 1)
        stop = (end - XIP + SECTOR - 1) & ~(SECTOR - 1)
        first = off
        while off < stop:
            seg = min(stop, (off | (BLOCK64 - 1)) + 1)
            # these bootrom functions return nothing (R0 is left as the first argument);
            # a failure shows up as a timeout here or in verify()
            self.swd.call(self.fn["erase"], (off, seg - off, BLOCK64, ERASE_BLOCK_CMD), sp=STACK_TOP, timeout_ms=ERASE_TIMEOUT_MS)
            off = seg
            yield (off - first) / (stop - first)

    def program(self, path, total):
        done = 0
        for base, buf in windows(path):
            self.swd.write_block(BUF, struct.unpack("<%dI" % (SECTOR // 4), buf))
            self.swd.call(self.fn["program"], (base - XIP, BUF, SECTOR), sp=STACK_TOP, timeout_ms=ERASE_TIMEOUT_MS)
            done += SECTOR
            yield min(1.0, done / total)

    def verify(self, path, total):
        done = 0
        for base, buf in windows(path):
            got = self.swd.read_block(base, SECTOR // 4)
            if struct.pack("<%dI" % (SECTOR // 4), *got) != bytes(buf):
                _fail("Verify failed at %08X" % base)
            done += SECTOR
            yield min(1.0, done / total)

    def erase_all(self):
        """Generator like run(): erase the whole flash so the chip is as it left the factory
        (all 0xFF, which makes the bootrom fall back to USB boot mode), then spot-check the
        first and last 256 bytes of every 64 KB block. Stages: 0 erase, 2 check, 3 done."""
        yield 0, 0.0
        self.prepare()
        try:
            for f in self.erase(XIP, XIP + self.flash_size):
                yield 0, f
        finally:
            self.finish()
        blocks = self.flash_size // BLOCK64
        for i in range(blocks):
            base = XIP + i * BLOCK64
            for addr in (base, base + BLOCK64 - 256):
                if any(w != 0xFFFFFFFF for w in self.swd.read_block(addr, 64)):
                    _fail("Erase failed at %08X" % addr)
            yield 2, (i + 1) / blocks
        yield 3, 1.0

    def run(self, path):
        info = uf2.check(path)
        total = ((info["end"] or 0) - ((info["start"] or 0) & ~(SECTOR - 1)) + SECTOR - 1) // SECTOR * SECTOR
        yield 0, 0.0
        self.prepare()   # may find the real flash size, so the size check comes after it
        try:
            if info["start"] is None or info["start"] < XIP or info["end"] > XIP + self.flash_size:
                _fail("File does not fit in flash", "image %s to %s, flash %d" % (info["start"], info["end"], self.flash_size))
            for f in self.erase(info["start"], info["end"]):
                yield 0, f
            for f in self.program(path, total):
                yield 1, f
        finally:
            self.finish()
        for f in self.verify(path, total):
            yield 2, f
        yield 3, 1.0
