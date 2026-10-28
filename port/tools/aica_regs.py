"""An AICA voice's registers (0x80 bytes, little-endian 16-bit registers at 4-byte steps, as the chip's register area
holds them) decoded into the fields the AICA manual names: the sample (SA, PCMS, LSA, LEA, LPCTL), the envelope
(AR, D1R, D2R, RR, DL, KRS, LPSLNK), the pitch (OCT, FNS), the LFO, the DSP send (ISEL, IMXL), the direct send
(DISDL, DIPAN), TL and the filter (Q, FLV0..4, FAR, FD1R, FD2R, FRR, LPOFF).
"""

FIELDS = (  # (name, register offset, high bit, low bit)
    ("SSCTL", 0x00, 10, 10), ("LPCTL", 0x00, 9, 9), ("PCMS", 0x00, 8, 7), ("SAH", 0x00, 6, 0), ("SAL", 0x04, 15, 0),
    ("LSA", 0x08, 15, 0), ("LEA", 0x0C, 15, 0),
    ("D2R", 0x10, 15, 11), ("D1R", 0x10, 10, 6), ("AR", 0x10, 4, 0),
    ("LPSLNK", 0x14, 14, 14), ("KRS", 0x14, 13, 10), ("DL", 0x14, 9, 5), ("RR", 0x14, 4, 0),
    ("OCT", 0x18, 14, 11), ("FNS", 0x18, 9, 0),
    ("LFORE", 0x1C, 15, 15), ("LFOF", 0x1C, 14, 10), ("PLFOWS", 0x1C, 9, 8), ("PLFOS", 0x1C, 7, 5),
    ("ALFOWS", 0x1C, 4, 3), ("ALFOS", 0x1C, 2, 0),
    ("IMXL", 0x20, 7, 4), ("ISEL", 0x20, 3, 0), ("DISDL", 0x24, 11, 8), ("DIPAN", 0x24, 4, 0),
    ("TL", 0x28, 15, 8), ("VOFF", 0x28, 6, 6), ("LPOFF", 0x28, 5, 5), ("Q", 0x28, 4, 0),
    ("FLV0", 0x2C, 12, 0), ("FLV1", 0x30, 12, 0), ("FLV2", 0x34, 12, 0), ("FLV3", 0x38, 12, 0), ("FLV4", 0x3C, 12, 0),
    ("FAR", 0x40, 12, 8), ("FD1R", 0x40, 4, 0), ("FD2R", 0x44, 12, 8), ("FRR", 0x44, 4, 0),
)
SHOWN = ("PCMS", "LPCTL", "AR", "D1R", "D2R", "DL", "RR", "KRS", "TL", "DISDL", "DIPAN", "ISEL", "IMXL", "LPOFF",
         "Q", "FLV0", "FLV1", "FLV2", "FLV3", "FLV4", "FAR", "FD1R", "FD2R", "FRR", "LFOF", "PLFOS", "ALFOS", "OCT",
         "FNS", "LSA", "LEA")


def fields(hexregs):
    """{name: value} for a voice's registers as hex text (0x80 bytes), or {} for '-'."""
    if not hexregs or hexregs == "-":
        return {}
    b = bytes.fromhex(hexregs)
    out = {}
    for name, off, hi, lo in FIELDS:
        w = b[off] | b[off + 1] << 8
        out[name] = (w >> lo) & ((1 << (hi - lo + 1)) - 1)
    out["SA"] = out.pop("SAH") << 16 | out.pop("SAL")
    return out


def decode(hexregs, shown=SHOWN):
    f = fields(hexregs)
    return " ".join(f"{k}={f[k]}" for k in shown if k in f)
