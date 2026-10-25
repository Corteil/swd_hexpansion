"""UF2 and raw .bin reading for the Flash tile.

blocks(path) yields (address, bytes) in file order without loading the whole file, so a
large image fits in the badge's RAM. check(path) walks the file once and reports what is
in it (blocks, size, address range, family) or raises UF2Error.
"""
import os
import struct

MAGIC0, MAGIC1, MAGIC_END = 0x0A324655, 0x9E5D5157, 0x0AB16F30
FLAG_FAMILY = 0x2000
FLAG_NOT_MAIN = 0x1
BLOCK = 512
BIN_BASE = 0x10000000  # RP2350 XIP flash, where a raw .bin is assumed to load


class UF2Error(Exception):
    pass


def _block(buf, index):
    if len(buf) != BLOCK:
        raise UF2Error("block %d is short" % index)
    m0, m1, flags, addr, size, num, total, fam = struct.unpack_from("<8I", buf, 0)
    if m0 != MAGIC0 or m1 != MAGIC1 or struct.unpack_from("<I", buf, BLOCK - 4)[0] != MAGIC_END:
        raise UF2Error("block %d has bad magic" % index)
    if size > 476:
        raise UF2Error("block %d payload too big" % index)
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
            yield addr, data


def check(path):
    """Summary dict for a file: raises UF2Error for a damaged .uf2."""
    if not path.lower().endswith(".uf2"):
        size = os.stat(path)[6]
        return {"kind": "bin", "bytes": size, "start": BIN_BASE, "end": BIN_BASE + size, "family": None, "blocks": 0}
    size = os.stat(path)[6]
    if size % BLOCK:
        raise UF2Error("size is not a multiple of 512")
    lo, hi, count, total, fam, nbytes = None, 0, 0, None, None, 0
    seen = set()
    with open(path, "rb") as f:
        i = 0
        while True:
            buf = f.read(BLOCK)
            if not buf:
                break
            flags, addr, num, tot, fm, data = _block(buf, i)
            i += 1
            if total is None:
                total = tot
            elif tot != total:
                raise UF2Error("block %d disagrees on total" % i)
            if num in seen:
                raise UF2Error("block number %d repeated" % num)
            seen.add(num)
            if flags & FLAG_FAMILY:
                fam = fm if fam is None else fam
                if fm != fam:
                    raise UF2Error("mixed families")
            if flags & FLAG_NOT_MAIN:
                continue
            count += 1
            nbytes += len(data)
            lo = addr if lo is None else min(lo, addr)
            hi = max(hi, addr + len(data))
    if total is not None and len(seen) != total:
        raise UF2Error("expected %d blocks, found %d" % (total, len(seen)))
    return {"kind": "uf2", "bytes": nbytes, "start": lo, "end": hi, "family": fam, "blocks": count}
