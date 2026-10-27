"""UF2 and raw .bin reading for the Flash tile.

The SDK's RP2350 UF2 files start with a block of the "absolute" family aimed at 0x10FFFF00
(the RP2350-E10 workaround for the bootrom's own UF2 loader). It is not part of the image
and lies outside the flash, so it is skipped here and has its own block count.

blocks(path) yields (address, bytes) in file order without loading the whole file, so a
large image fits in the badge's RAM. check(path) walks the file once and reports what is
in it (blocks, size, address range, family) or raises UF2Error.
"""
import os
import struct

MAGIC0, MAGIC1, MAGIC_END = 0x0A324655, 0x9E5D5157, 0x0AB16F30
FLAG_FAMILY = 0x2000
FLAG_NOT_MAIN = 0x1
FAMILY_ABSOLUTE = 0xE48BFF57
BLOCK = 512
BIN_BASE = 0x10000000  # XIP flash on both chips, where a raw .bin is assumed to load
HEAD = 4096            # bytes from the flash base kept for the boot-header checks

FAMILY_RP2040, FAMILY_DATA = 0xE48BFF56, 0xE48BFF58
FAMILY_RP2350_ARM_S, FAMILY_RP2350_RISCV, FAMILY_RP2350_ARM_NS = 0xE48BFF59, 0xE48BFF5A, 0xE48BFF5B
FAMILY_NAMES = {
    FAMILY_RP2040: "RP2040", FAMILY_ABSOLUTE: "absolute", FAMILY_DATA: "data",
    FAMILY_RP2350_ARM_S: "RP2350 ARM-S", FAMILY_RP2350_RISCV: "RP2350 RISC-V", FAMILY_RP2350_ARM_NS: "RP2350 ARM-NS",
}


def chip_of(fam):
    """The chip a UF2 family is for ("RP2040" or "RP2350"), or None for the data and absolute families."""
    if fam == FAMILY_RP2040:
        return "RP2040"
    if fam in (FAMILY_RP2350_ARM_S, FAMILY_RP2350_RISCV, FAMILY_RP2350_ARM_NS):
        return "RP2350"
    return None


def family_name(fam):
    return FAMILY_NAMES.get(fam, "family %08X" % fam)


class UF2Error(Exception):
    pass


def _bad(msg, detail=""):
    """Raise UF2Error(msg); msg is shown to the user, detail goes to the USB console."""
    print("uf2:", msg, detail)
    raise UF2Error(msg)


def _block(buf, index):
    if len(buf) != BLOCK:
        _bad("Not a valid UF2 file", "block %d is short" % index)
    m0, m1, flags, addr, size, num, total, fam = struct.unpack_from("<8I", buf, 0)
    if m0 != MAGIC0 or m1 != MAGIC1 or struct.unpack_from("<I", buf, BLOCK - 4)[0] != MAGIC_END:
        _bad("Not a valid UF2 file", "block %d has bad magic" % index)
    if size > 476:
        _bad("Not a valid UF2 file", "block %d payload too big" % index)
    return flags, addr, num, total, fam, buf[32:32 + size]


def blocks(path, skip_not_main=True):
    """Yield (address, data) for each data block of a .uf2, or the whole of a .bin at BIN_BASE."""
    if not path.lower().endswith(".uf2"):
        with open(path, "rb") as f:
            addr = BIN_BASE
            while True:
                chunk = f.read(256)
                if not chunk:
                    return
                yield addr, chunk
                addr += len(chunk)
    with open(path, "rb") as f:
        i = 0
        while True:
            buf = f.read(BLOCK)
            if not buf:
                return
            flags, addr, _num, _total, _fam, data = _block(buf, i)
            i += 1
            if skip_not_main and flags & FLAG_NOT_MAIN:
                continue
            if flags & FLAG_FAMILY and _fam == FAMILY_ABSOLUTE:
                continue
            yield addr, data


def check(path):
    """Summary dict for a file: raises UF2Error for a damaged .uf2."""
    if not path.lower().endswith(".uf2"):
        size = os.stat(path)[6]
        with open(path, "rb") as f:
            head = f.read(HEAD)
        return {"kind": "bin", "bytes": size, "start": BIN_BASE, "end": BIN_BASE + size, "family": None,
                "blocks": 0, "head": head, "head_have": [(0, len(head))]}
    size = os.stat(path)[6]
    if size % BLOCK:
        _bad("UF2 file is damaged", "size is not a multiple of 512")
    lo, hi, count, fam, nbytes = None, 0, 0, None, 0
    head, have = bytearray(HEAD), []   # the image's first HEAD bytes at the flash base, and which of them are present
    totals = {}   # family -> block count the file claims
    seen = {}     # family -> set of block numbers
    with open(path, "rb") as f:
        i = 0
        while True:
            buf = f.read(BLOCK)
            if not buf:
                break
            flags, addr, num, tot, fm, data = _block(buf, i)
            i += 1
            key = fm if flags & FLAG_FAMILY else 0
            if totals.setdefault(key, tot) != tot:
                _bad("UF2 file is damaged", "block %d disagrees on total" % i)
            nums = seen.setdefault(key, set())
            if num in nums:
                _bad("UF2 file is damaged", "block number %d repeated" % num)
            nums.add(num)
            if key == FAMILY_ABSOLUTE or flags & FLAG_NOT_MAIN:
                continue
            if key and fam is None:
                fam = key
            count += 1
            nbytes += len(data)
            lo = addr if lo is None else min(lo, addr)
            hi = max(hi, addr + len(data))
            if BIN_BASE <= addr < BIN_BASE + HEAD:
                off = addr - BIN_BASE
                n = min(len(data), HEAD - off)
                head[off:off + n] = data[:n]
                have.append((off, n))
    for key, total in totals.items():
        if key != FAMILY_ABSOLUTE and len(seen[key]) != total:
            _bad("UF2 file is incomplete", "expected %d blocks, found %d" % (total, len(seen[key])))
    return {"kind": "uf2", "bytes": nbytes, "start": lo, "end": hi, "family": fam, "blocks": count,
            "head": bytes(head), "head_have": have}
