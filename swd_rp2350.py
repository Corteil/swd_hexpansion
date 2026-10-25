# swd_rp2350.py - MicroPython SWD bit-bang sketch (multidrop) for RP2350
# Runs on the badge's ESP32-S3. UNTESTED: check timings and pins on real hardware.
# Slot B pins -> RP2350 debug header on slot A's hex. Replace with real GPIOs.
from machine import Pin
import time

SWCLK_PIN = 1   # TODO: direct ESP32-S3 GPIO on slot B
SWDIO_PIN = 2   # TODO: direct ESP32-S3 GPIO on slot B

# RP2350 TARGETSEL values (check against the datasheet for your silicon)
TARGET_CORE0 = 0x01002927
TARGET_CORE1 = 0x11002927
TARGET_RESCUE = 0xF1002927

ACK_OK, ACK_WAIT, ACK_FAULT = 1, 2, 4

# DP registers (bank 0 unless noted)
DP_DPIDR, DP_ABORT = 0x0, 0x0
DP_CTRLSTAT, DP_SELECT, DP_RDBUFF = 0x4, 0x8, 0xC
# ADIv6 AP addresses on RP2350 (from OpenOCD rp2350.cfg: -ap-num 0x2000 / 0x4000)
AP_BASE_CORE0, AP_BASE_CORE1 = 0x2000, 0x4000
# ADIv6 MEM-AP register offsets within an AP (standard layout, unverified on RP2350)
MEMAP_CSW, MEMAP_TAR, MEMAP_DRW = 0xD00, 0xD04, 0xD0C


class SWDError(Exception):
    pass


class SWD:
    def __init__(self, clk=SWCLK_PIN, dio=SWDIO_PIN, delay_us=0):
        # delay_us: extra half-period delay per clock edge; raise it if the
        # ESP32-S3 bit-bang is too fast for the target or the wiring
        self.delay_us = delay_us
        self.select = None   # cached DP SELECT value
        # Start tri-stated so the unpowered RP2350 isn't back-fed
        self.clk = Pin(clk, Pin.IN)
        self.dio = Pin(dio, Pin.IN)
        self.active = False
        self.ap_base = AP_BASE_CORE0

    # ---- pin control ----
    def enable(self):
        """Call once slot A (RP2350) is powered."""
        self.clk.init(Pin.OUT, value=0)
        self.dio.init(Pin.OUT, value=1)
        self.active = True

    def disable(self):
        self.clk.init(Pin.IN)
        self.dio.init(Pin.IN)
        self.active = False

    # ---- bit level ----
    def _pulse(self):
        d = self.delay_us
        self.clk(1)
        if d:
            time.sleep_us(d)
        self.clk(0)
        if d:
            time.sleep_us(d)

    def _write_bits(self, value, n):
        # set the latch to the first bit BEFORE switching to output (no glitch)
        self.dio.value(value & 1)
        self.dio.init(Pin.OUT)
        for i in range(n):
            self.dio((value >> i) & 1)   # LSB first, data set while clk low
            self._pulse()

    def _read_bits(self, n):
        self.dio.init(Pin.IN)
        v = 0
        for i in range(n):
            # target changes data on rising edge; sample before the pulse's rising edge
            v |= self.dio() << i
            self._pulse()
        return v

    def _turnaround(self):
        self.dio.init(Pin.IN)
        self._pulse()

    # ---- line reset / selection ----
    def line_reset(self):
        self._write_bits(0xFFFFFFFF, 32)
        self._write_bits(0x0003FFFF, 32)   # >=50 high clocks, then idle
        self._write_bits(0, 8)

    @staticmethod
    def _parity(v):
        return bin(v).count("1") & 1

    @staticmethod
    def _request(apndp, rnw, addr):
        a2 = (addr >> 2) & 1
        a3 = (addr >> 3) & 1
        parity = (apndp + rnw + a2 + a3) & 1
        # start=1, APnDP, RnW, A2, A3, parity, stop=0, park=1  (LSB first)
        return 1 | (apndp << 1) | (rnw << 2) | (a2 << 3) | (a3 << 4) | (parity << 5) | (1 << 7)

    def dormant_to_swd(self):
        """ARM dormant-to-SWD wake sequence (same bytes OpenOCD uses):
        >=8 high clocks, 128-bit selection alert, 4 low clocks,
        8-bit SWD activation code 0x1A, then a line reset."""
        alert = (0x92, 0xF3, 0x09, 0x62, 0x95, 0x2D, 0x85, 0x86,
                 0xE9, 0xAF, 0xDD, 0xE3, 0xA2, 0x0E, 0xBC, 0x19)
        self._write_bits(0xFF, 8)
        for b in alert:
            self._write_bits(b, 8)
        self._write_bits(0, 4)
        self._write_bits(0x1A, 8)
        self.line_reset()

    def target_select(self, targetsel=TARGET_CORE0):
        """Multidrop: line reset, then write DP TARGETSEL (addr 0xC).
        The selected target does NOT ACK this write, so ignore the ACK bits."""
        self.line_reset()
        self._write_bits(self._request(0, 0, 0xC), 8)
        self._turnaround()
        self._read_bits(3)            # ACK phase: not driven, ignore
        self._turnaround()
        self._write_bits(targetsel, 32)
        self._write_bits(self._parity(targetsel), 1)
        self._write_bits(0, 8)        # idle cycles before the next request
        self.select = None            # SELECT resets on target change

    # ---- transfers ----
    def _xfer(self, apndp, rnw, addr, data=0, retries=8):
        for _ in range(retries):
            self._write_bits(self._request(apndp, rnw, addr), 8)
            self._turnaround()
            ack = self._read_bits(3)
            if ack == ACK_OK:
                if rnw:
                    val = self._read_bits(32)
                    par = self._read_bits(1)
                    self._turnaround()
                    self._write_bits(0, 8)   # idle cycles
                    if par != self._parity(val):
                        raise SWDError("parity error")
                    return val
                else:
                    self._turnaround()
                    self._write_bits(data, 32)
                    self._write_bits(self._parity(data), 1)
                    self._write_bits(0, 8)
                    return None
            if ack == ACK_WAIT or ack == ACK_FAULT:
                # Like debugprobe/CMSIS-DAP: clock through the data phase
                # (discard 33 bits on a read, send 33 zeros on a write) so the
                # target stays in step even if overrun detection is on.
                if rnw:
                    self._read_bits(33)
                    self._turnaround()
                else:
                    self._turnaround()
                    self._write_bits(0, 32)
                    self._write_bits(0, 1)
                self._write_bits(0, 8)
                if ack == ACK_WAIT:
                    time.sleep_us(50)
                    continue
                # FAULT: sticky error, clear it so the next transfer can work
                self._xfer(0, 0, DP_ABORT, 0x1E, retries=1)
                raise SWDError("FAULT (sticky errors cleared)")
            self._turnaround()
            # ACK 0b111 (all ones) = nothing drove the line: wiring/power/state
            raise SWDError("bad ACK=%d (no target?)" % ack)
        raise SWDError("too many WAITs")

    def dp_read(self, addr):
        return self._xfer(0, 1, addr)

    def dp_write(self, addr, val):
        self._xfer(0, 0, addr, val)
        if addr == DP_SELECT:
            self.select = val

    def ap_read(self, addr):
        self._xfer(1, 1, addr)               # posted; real data comes next
        return self.dp_read(DP_RDBUFF)

    def ap_write(self, addr, val):
        self._xfer(1, 0, addr, val)

    # ---- bring-up ----
    def _try_select(self, targetsel, dormant):
        if dormant:
            self.dormant_to_swd()
        self.target_select(targetsel)
        return self.dp_read(DP_DPIDR)

    def connect(self, targetsel=TARGET_CORE0):
        self.enable()
        # Try plain SWD first; if nothing answers, wake from dormant and retry.
        # (Which one RP2350 needs after reset: confirm with the OpenOCD -d3 log.)
        try:
            dpidr = self._try_select(targetsel, False)
        except SWDError:
            dpidr = self._try_select(targetsel, True)
        self.dp_write(DP_ABORT, 0x1E)        # clear sticky errors
        self.dp_write(DP_SELECT, 0)
        self.dp_write(DP_CTRLSTAT, 0x50000000)   # CDBGPWRUPREQ | CSYSPWRUPREQ
        for _ in range(100):
            if self.dp_read(DP_CTRLSTAT) & 0xA0000000 == 0xA0000000:
                break
            time.sleep_ms(1)
        else:
            raise SWDError("debug power-up timeout")
        return dpidr

    # ---- memory access via MEM-AP (32-bit, auto-increment) ----
    # RP2350 is ADIv6 (DPv3). OpenOCD's rp2350.cfg uses -adiv6 with core0 AP at
    # 0x2000 and core1 AP at 0x4000, so AP registers are addressed through
    # DP SELECT = (ap_base + register offset), not "AP 0 bank N" as in ADIv5.
    # UNVERIFIED: MEM-AP register offsets below (CSW/TAR/DRW at +0xD00/+0xD04/+0xD0C)
    # are the ADIv6 standard layout; confirm with OpenOCD -d3 against real hardware.
    def _ap_sel(self, offset):
        # SELECT[31:4] = AP address, SELECT[3:0] = DPBANKSEL (kept 0 so the
        # CTRLSTAT/DPIDR accesses elsewhere stay on bank 0)
        sel = (self.ap_base + offset) & 0xFFFFFFF0
        if sel != self.select:
            self.dp_write(DP_SELECT, sel)
            self.select = sel
        return offset & 0xC

    def _mem_reg_write(self, offset, val):
        self.ap_write(self._ap_sel(offset), val)

    def _mem_reg_read(self, offset):
        return self.ap_read(self._ap_sel(offset))

    def mem_setup(self, ap_base=AP_BASE_CORE0):
        self.ap_base = ap_base
        self._mem_reg_write(MEMAP_CSW, 0x23000012)   # 32-bit, auto-inc; check bits for RP2350

    def read32(self, addr):
        self._mem_reg_write(MEMAP_TAR, addr)
        return self._mem_reg_read(MEMAP_DRW)

    def write32(self, addr, val):
        self._mem_reg_write(MEMAP_TAR, addr)
        self._mem_reg_write(MEMAP_DRW, val)


if __name__ == "__main__":
    swd = SWD()
    try:
        print("DPIDR: 0x%08X" % swd.connect(TARGET_CORE0))
        swd.mem_setup()
        print("ROM[0]: 0x%08X" % swd.read32(0x00000000))
    finally:
        swd.disable()
