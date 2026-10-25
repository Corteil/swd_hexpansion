"""Turn a sigrok .sr capture of SWCLK/SWDIO into the bit stream the target saw.

  swd_bits.py CAPTURE.sr              print the bits, one burst per line
  swd_bits.py A.sr B.sr               also report where the two streams first differ

Bits are SWDIO sampled on each rising SWCLK edge, in the order sent, so they can be read
against the SWD spec (LSB first). A new line starts where the clock pauses for more than
GAP_US. Channel 0 must be SWCLK and channel 1 SWDIO. sigrok's own SWD decoder does not
understand multidrop (the RP2350's TARGETSEL), so this works on raw bits instead.
"""
import json
import sys
import zipfile

import numpy as np

GAP_US = 50


def load(path):
    """(samples masked to bits 0-1, samplerate in Hz) from a .sr file."""
    with zipfile.ZipFile(path) as z:
        meta = z.read("metadata").decode()
        rate = 0
        unit = 1
        for line in meta.splitlines():
            if line.startswith("samplerate="):
                s = line.split("=", 1)[1].strip().lower()
                mult = {"k": 1e3, "m": 1e6, "g": 1e9}.get(s.split()[-1][0], 1) if s.split()[-1][0].isalpha() else 1
                rate = float(s.split()[0]) * mult if len(s.split()) > 1 else float(s.rstrip("kmg")) * mult
            if line.startswith("unitsize="):
                unit = int(line.split("=")[1])
        names = sorted(n for n in z.namelist() if n.startswith("logic-1"))
        data = b"".join(z.read(n) for n in names)
    if unit not in (1, 2, 4):
        raise SystemExit("unsupported sample size %d" % unit)
    # only channels 0 and 1 matter, and they are in the low bits of each little-endian sample
    return np.frombuffer(data, dtype={1: "<u1", 2: "<u2", 4: "<u4"}[unit]) & 3, rate


def bursts(samples, rate):
    clk = samples & 1
    dio = (samples >> 1) & 1
    rising = np.flatnonzero((clk[1:] == 1) & (clk[:-1] == 0)) + 1
    if rising.size == 0:
        return []
    gap = max(1, int(GAP_US * 1e-6 * rate))
    cuts = np.flatnonzero(np.diff(rising) > gap) + 1
    out = []
    for chunk in np.split(rising, cuts):
        out.append("".join(str(int(b)) for b in dio[chunk - 1]))  # sample just before the edge
    return out


def show(path):
    samples, rate = load(path)
    lines = bursts(samples, rate)
    print("%s: %d samples at %.0f Hz, %d bursts" % (path, samples.size, rate, len(lines)))
    for i, b in enumerate(lines):
        grouped = " ".join(b[k:k + 8] for k in range(0, len(b), 8))
        print("%3d (%4d bits) %s" % (i, len(b), grouped))
    return "".join(lines)


def main(argv):
    if not argv or len(argv) > 2:
        raise SystemExit(__doc__)
    a = show(argv[0])
    if len(argv) == 2:
        b = show(argv[1])
        n = min(len(a), len(b))
        diff = next((i for i in range(n) if a[i] != b[i]), None)
        print("streams %s" % ("match for %d bits" % n if diff is None else "first differ at bit %d" % diff))


if __name__ == "__main__":
    main(sys.argv[1:])
