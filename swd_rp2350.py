# swd_rp2350.py - MicroPython SWD bit-bang (multidrop) for RP2350
# Runs on the badge's ESP32-S3. Tested against a Pico 2 (matches OpenOCD + Debug Probe).
# SWCLK = HS_F, SWDIO = HS_G of the hexpansion port the proto board is in
# (SWD.from_port). There is no reset line: reset is done over SWD (see reset()).
from machine import Pin
from array import array
import micropython
import time

# RP2350 multidrop TARGETSEL: OpenOCD's rp2350.cfg uses -dp-id 0x00040927 with the
# instance number in bits 31:28. A bench test on a Pico 2 did NOT need it: after a line
# reset the chip answers a plain DPIDR read (OpenOCD logs "swd_connect_single" too), so
# connect() is called with targetsel=None. The values below are for multidrop/rescue.
# (RP2040's are 0x01002927 / 0x11002927 / 0xF1002927.)
TARGET_CORE0 = 0x00040927
TARGET_CORE1 = 0x10040927
TARGET_RESCUE = 0xF0040927

ACK_OK, ACK_WAIT, ACK_FAULT = 1, 2, 4

# DP registers (bank 0 unless noted)
DP_DPIDR, DP_ABORT = 0x0, 0x0
DP_CTRLSTAT, DP_SELECT, DP_RDBUFF = 0x4, 0x8, 0xC
# ADIv6 AP addresses on RP2350 (from OpenOCD rp2350.cfg: -ap-num 0x2000 / 0x4000)
AP_BASE_CORE0, AP_BASE_CORE1 = 0x2000, 0x4000
# ADIv6 MEM-AP register offsets within an AP (standard layout, unverified on RP2350)
MEMAP_CSW, MEMAP_TAR, MEMAP_DRW = 0xD00, 0xD04, 0xD0C

# Cortex-M debug registers
DHCSR, DCRSR, DCRDR, AIRCR = 0xE000EDF0, 0xE000EDF4, 0xE000EDF8, 0xE000ED0C
DBGKEY = 0xA05F0000
C_DEBUGEN, C_HALT, C_STEP, C_MASKINTS = 1, 2, 4, 8
S_REGRDY, S_HALT = 1 << 16, 1 << 17
AIRCR_RESET = 0x05FA0004   # VECTKEY | SYSRESETREQ
DEMCR = 0xE000EDFC
DEMCR_TRCENA, DEMCR_VC_CORERESET = 1 << 24, 1


class SWDError(Exception):
    pass


def _fail(msg, detail=""):
    """Raise SWDError(msg); msg is shown to the user, detail goes to the USB console."""
    print("swd:", msg, detail)
    raise SWDError(msg)


# GPIO block of the ESP32-S3 (word indexes from 0x60004000, bank 0 / bank 1): OUT set
# 2/5, OUT clear 3/6, output-enable set 9/12, clear 10/13, input 15/16. Everything the
# bit loop needs lives in one array so a call passes a single argument:
#   c[0] SWCLK mask  c[1] SWDIO mask  c[2] busy-wait count per half clock
#   c[3..7] register indexes: ts tc ets etc in      c[8] read data  c[9] parity ok
@micropython.viper
def _out(c: ptr32, val: int, n: int):
    p = ptr32(0x60004000)
    clkm = c[0]
    diom = c[1]
    dly = c[2]
    ts = c[3]
    tc = c[4]
    # data first, then drive it, so the line never glitches
    if val & 1:
        p[ts] = diom
    else:
        p[tc] = diom
    p[c[5]] = diom
    i = 0
    while i < n:
        if (val >> i) & 1:
            p[ts] = diom
        else:
            p[tc] = diom
        p[ts] = clkm
        d = 0
        while d < dly:
            d += 1
        p[tc] = clkm
        d = 0
        while d < dly:
            d += 1
        i += 1


@micropython.viper
def _in(c: ptr32, n: int) -> int:
    p = ptr32(0x60004000)
    clkm = c[0]
    diom = c[1]
    dly = c[2]
    ts = c[3]
    tc = c[4]
    inn = c[7]
    p[c[6]] = diom   # release the line to the target
    v = 0
    i = 0
    while i < n:
        if p[inn] & diom:   # sample, then clock: same order as the slow version that matched OpenOCD
            v |= 1 << i
        p[ts] = clkm
        d = 0
        while d < dly:
            d += 1
        p[tc] = clkm
        d = 0
        while d < dly:
            d += 1
        i += 1
    return v


@micropython.viper
def _parity(v: int) -> int:
    v ^= v >> 16
    v ^= v >> 8
    v ^= v >> 4
    v ^= v >> 2
    v ^= v >> 1
    return v & 1


@micropython.viper
def _xfer_fast(cfg, req: int, wdata: int, rnw: int) -> int:
    """One whole SWD transfer. Returns the ACK; read data is left in c[8], parity ok in c[9].
    After WAIT/FAULT the data phase is still clocked through (as debugprobe does) so the
    target stays in step. (cfg stays an object so it can be handed on to _in/_out; calls
    between viper functions return objects, hence the int() casts.)"""
    c = ptr32(cfg)
    _out(cfg, req, 8)
    _in(cfg, 1)
    ack = int(_in(cfg, 3))
    if ack == 1:
        if rnw:
            data = int(_in(cfg, 32))
            par = int(_in(cfg, 1))
            _in(cfg, 1)
            c[8] = data
            if int(_parity(data)) == par:
                c[9] = 1
            else:
                c[9] = 0
        else:
            _in(cfg, 1)
            _out(cfg, wdata, 32)
            _out(cfg, int(_parity(wdata)), 1)
        _out(cfg, 0, 8)
    elif ack == 2 or ack == 4:
        if rnw:
            _in(cfg, 32)
            _in(cfg, 1)
        else:
            _in(cfg, 1)
            _out(cfg, 0, 32)
            _out(cfg, 0, 1)
        _out(cfg, 0, 8)
    else:
        _in(cfg, 1)
    return ack


class SWD:
    @classmethod
    def from_port(cls, port, delay_us=0):
        """SWCLK on HS_F and SWDIO on HS_G of a hexpansion port (1-6)."""
        from system.hexpansion.config import HexpansionConfig, _pin_mapping
        cfg = HexpansionConfig(port)
        hs = _pin_mapping[port]["hs"]
        return cls(cfg.pin[0], cfg.pin[1], delay_us, gpio=(hs[0], hs[1]))

    def __init__(self, clk, dio, delay_us=0, gpio=None):
        # delay_us: extra half-period delay per clock edge; raise it if the
        # ESP32-S3 bit-bang is too fast for the target or the wiring
        if gpio is None:
            raise ValueError("gpio=(swclk, swdio) GPIO numbers are needed for the register fast path")
        self._setup_regs(gpio[0], gpio[1])
        self.delay_us = delay_us
        self.select = None   # cached DP SELECT value
        # Start tri-stated so the unpowered RP2350 isn't back-fed
        self.clk = clk if isinstance(clk, Pin) else Pin(clk, Pin.IN)
        self.dio = dio if isinstance(dio, Pin) else Pin(dio, Pin.IN)
        self.clk.init(Pin.IN)
        self.dio.init(Pin.IN)
        self.active = False
        self.activity = 0   # bumped once per transfer; the UI turns this into the SWD LED
        self.ap_base = AP_BASE_CORE0
        self.adiv = 6
        self.regs = (MEMAP_CSW, MEMAP_TAR, MEMAP_DRW)

    def _setup_regs(self, clk_gpio, dio_gpio):
        if (clk_gpio >= 32) != (dio_gpio >= 32):
            raise ValueError("SWCLK and SWDIO must be in the same GPIO bank")
        regs = (2, 3, 9, 10, 15) if clk_gpio < 32 else (5, 6, 12, 13, 16)
        self._cfg = array("I", [1 << (clk_gpio & 31), 1 << (dio_gpio & 31), 0] + list(regs) + [0, 0])
        # request byte for every (APnDP, RnW, A[3:2]): start, APnDP, RnW, A2, A3, parity, stop, park
        self._reqs = [self._request(i >> 3, (i >> 2) & 1, (i & 3) << 2) for i in range(16)]

    @property
    def delay_us(self):
        return self._delay_us

    @delay_us.setter
    def delay_us(self, us):
        """Extra half-clock delay; 0 is the fastest the bit-bang goes (~500 kHz)."""
        self._delay_us = us
        self._cfg[2] = us * 50   # ~15 ns per busy-wait iteration at 240 MHz

    # ---- pin control ----
    # Bit-banging goes straight to the ESP32-S3 GPIO registers from viper code: a
    # Pin.value() call per edge limited the clock to ~100 kHz, and Pin.init() for every
    # direction change cost more than the data itself. Pin objects are only used to set
    # the pins up (function = plain GPIO) and to hand them back.
    def enable(self):
        """Call once the target is powered."""
        self.clk.init(Pin.OUT, value=0)
        self.dio.init(Pin.OUT, value=1)
        self.dio.init(Pin.IN)   # released until a transfer drives it
        self.active = True

    def disable(self):
        self.clk.init(Pin.IN)
        self.dio.init(Pin.IN)
        self.active = False

    # ---- bit level ----
    def _write_bits(self, value, n):
        _out(self._cfg, value, n)

    def _read_bits(self, n):
        return _in(self._cfg, n) & 0xFFFFFFFF

    def _turnaround(self):
        _in(self._cfg, 1)

    # ---- line reset / selection ----
    def line_reset(self):
        self._write_bits(0xFFFFFFFF, 32)
        self._write_bits(0x0003FFFF, 32)   # >=50 high clocks, then idle
        self._write_bits(0, 8)

    @staticmethod
    def _par(v):
        return _parity(v)

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
        self._write_bits(self._par(targetsel), 1)
        self._write_bits(0, 8)        # idle cycles before the next request
        self.select = None            # SELECT resets on target change

    # ---- transfers ----
    def _xfer(self, apndp, rnw, addr, data=0, retries=8):
        self.activity += 1
        req = self._reqs[(apndp << 3) | (rnw << 2) | ((addr >> 2) & 3)]
        cfg = self._cfg
        for _ in range(retries):
            ack = _xfer_fast(cfg, req, data, rnw)
            if ack == ACK_OK:
                if rnw:
                    if not cfg[9]:
                        _fail("Bad data on the SWD line", "parity error")
                    return cfg[8]
                return None
            if ack == ACK_WAIT:
                time.sleep_us(50)
                continue
            if ack == ACK_FAULT:
                # sticky error: clear it so the next transfer can work
                self._xfer(0, 0, DP_ABORT, 0x1E, retries=1)
                _fail("Target reported an error", "FAULT, sticky errors cleared")
            # ACK 0b111 (all ones) = nothing drove the line: wiring/power/state
            _fail("No reply: check wiring and power", "bad ACK=%d" % ack)
        _fail("Target busy, gave up", "too many WAITs")

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
        if targetsel is None:   # single-drop target: a line reset is enough
            self.line_reset()
            self.select = None
        else:
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
            _fail("Target debug will not power up", "CTRLSTAT power-up ack timeout")
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
        if self.adiv == 5:   # APSEL in SELECT[31:24], bank in SELECT[7:4]
            sel = self.ap_base | (offset & 0xF0)
        else:
            sel = (self.ap_base + offset) & 0xFFFFFFF0
        if sel != self.select:
            self.dp_write(DP_SELECT, sel)
            self.select = sel
        return offset & 0xC

    def _mem_reg_write(self, offset, val):
        self.ap_write(self._ap_sel(offset), val)

    def _mem_reg_read(self, offset):
        return self.ap_read(self._ap_sel(offset))

    def mem_setup(self, ap_base=AP_BASE_CORE0, adiv=6):
        self.ap_base = ap_base
        self.adiv = adiv
        self.regs = (MEMAP_CSW, MEMAP_TAR, MEMAP_DRW) if adiv == 6 else (0x00, 0x04, 0x0C)
        self._mem_reg_write(self.regs[0], 0x23000012)   # 32-bit, auto-inc; check bits for RP2350

    def read32(self, addr):
        self._mem_reg_write(self.regs[1], addr)
        return self._mem_reg_read(self.regs[2])

    def write32(self, addr, val):
        self._mem_reg_write(self.regs[1], addr)
        self._mem_reg_write(self.regs[2], val)

    # The MEM-AP auto-increments TAR but only within a 1 KB window, so bursts are split
    # at 1 KB boundaries. SELECT is set once per burst and the DRW access repeated.
    def read_block(self, addr, n):
        """n 32-bit words from addr, as a list."""
        out = []
        while n:
            chunk = min(n, (0x400 - (addr & 0x3FF)) >> 2)
            self._mem_reg_write(self.regs[1], addr)
            reg = self._ap_sel(self.regs[2])
            self._xfer(1, 1, reg)                    # posted read: its data arrives with the next
            for _ in range(chunk - 1):
                out.append(self._xfer(1, 1, reg))
            out.append(self.dp_read(DP_RDBUFF))
            addr += 4 * chunk
            n -= chunk
        return out

    def write_block(self, addr, words):
        """Write 32-bit words from addr. Raises SWDError if the target flagged an error."""
        i = 0
        while i < len(words):
            chunk = min(len(words) - i, (0x400 - (addr & 0x3FF)) >> 2)
            self._mem_reg_write(self.regs[1], addr)
            reg = self._ap_sel(self.regs[2])
            for k in range(chunk):
                self._xfer(1, 0, reg, words[i + k])
            addr += 4 * chunk
            i += chunk
        if self.dp_read(DP_CTRLSTAT) & 0x20:         # STICKYERR
            self.dp_write(DP_ABORT, 0x1E)
            _fail("Memory write failed", "STICKYERR")

    # ---- core control (Cortex-M33 debug registers) ----
    def halt(self):
        self.write32(DHCSR, DBGKEY | C_DEBUGEN | C_HALT)
        for _ in range(50):
            if self.read32(DHCSR) & S_HALT:
                return
            time.sleep_ms(1)
        _fail("Could not halt the CPU", "DHCSR.S_HALT not set")

    def resume(self):
        self.write32(DHCSR, DBGKEY | C_DEBUGEN)

    def step(self):
        self.write32(DHCSR, DBGKEY | C_DEBUGEN | C_STEP)

    def is_halted(self):
        return bool(self.read32(DHCSR) & S_HALT)

    def reset(self):
        """SYSRESETREQ through AIRCR. The link may drop, so callers reconnect afterwards."""
        self.write32(AIRCR, AIRCR_RESET)

    def write_reg(self, n, val):
        """Set core register n (0-12, 13=SP, 14=LR, 15=PC, 16=xPSR) of a halted core."""
        self.write32(DCRDR, val)
        self.write32(DCRSR, 0x10000 | n)   # bit 16: write
        for _ in range(50):
            if self.read32(DHCSR) & S_REGRDY:
                return
            time.sleep_ms(1)
        _fail("CPU register access timed out", "DCRSR write, S_REGRDY not set")

    def call(self, func, args=(), sp=0x20041000, bkpt=0x20040000, timeout_ms=5000):
        """Run func(*args) on the halted core and return R0. The function returns into a
        BKPT planted at `bkpt`, which halts the core again. Interrupts are masked meanwhile."""
        self.write32(bkpt, 0xBE00BE00)
        for i, a in enumerate(args):
            self.write_reg(i, a)
        self.write_reg(13, sp)
        self.write_reg(14, bkpt | 1)
        self.write_reg(16, 0x01000000)     # Thumb state, no exception active
        self.write_reg(15, func & ~1)
        self.write32(DHCSR, DBGKEY | C_DEBUGEN | C_MASKINTS)
        t = time.ticks_ms()
        while not self.read32(DHCSR) & S_HALT:
            if time.ticks_diff(time.ticks_ms(), t) > timeout_ms:
                self.write32(DHCSR, DBGKEY | C_DEBUGEN | C_HALT)
                _fail("Flash routine timed out", "call to %08X" % func)
        return self.read_reg(0)

    def release_core1(self):
        """Let core 1 run. A debugger session (OpenOCD halts both cores) can leave it halted,
        or with a reset vector catch that halts it on every reset; the timers pause while
        either core is halted, so a program that waits on a timer then never gets going.
        Only meaningful for the ADIv6 RP2350 (core 1 is the AP at 0x4000)."""
        if self.adiv != 6:
            return
        core0 = self.ap_base
        try:
            self.mem_setup(AP_BASE_CORE1, 6)
            self.write32(DEMCR, DEMCR_TRCENA)
            self.write32(DHCSR, DBGKEY)   # no halt, debug off: run
        finally:
            self.mem_setup(core0, 6)

    def reset_halt(self, targetsel=None):
        """Reset the chip and catch the core at its first instruction (DEMCR.VC_CORERESET), so
        it is in a clean state: no fault, no stack limit. Reconnects afterwards."""
        self.write32(DEMCR, DEMCR_TRCENA | DEMCR_VC_CORERESET)
        self.write32(DHCSR, DBGKEY | C_DEBUGEN)
        self.reset()
        time.sleep_ms(100)
        self.connect(targetsel)
        self.mem_setup(self.ap_base, self.adiv)
        for _ in range(100):
            if self.read32(DHCSR) & S_HALT:
                break
            time.sleep_ms(5)
        else:
            _fail("Could not halt the CPU", "no halt out of reset")
        self.write32(DEMCR, DEMCR_TRCENA)

    def rescue(self):
        """RP2350 rescue reset: pulse RESCUE_RESTART in the RP_AP CTRL register (ADIv6 AP at
        0x80000, same as OpenOCD's rescue_reset). Resets everything except the DP and RP_AP and
        leaves both cores in the bootrom, so it recovers a chip whose firmware has locked the
        cores or switched off the debug access. Needs a live DP (connect() first); reconnect
        afterwards."""
        self.dp_write(DP_SELECT, 0x80000)
        self.ap_write(0, 0x80000000)
        self.ap_write(0, 0)
        self.select = None

    def read_reg(self, n):
        """Core register n (0-12, 13=SP, 14=LR, 15=PC) of a halted core."""
        self.write32(DCRSR, n)
        for _ in range(50):
            if self.read32(DHCSR) & S_REGRDY:
                return self.read32(DCRDR)
            time.sleep_ms(1)
        _fail("CPU register access timed out", "DCRSR read, S_REGRDY not set")
