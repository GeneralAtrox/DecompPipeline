"""Sound data the AICA plays, read from files: the sample banks of Sega's Dreamcast/NAOMI sound driver ("DTPK"), the
NAOMI stream files ("SPSD"), and the chip's sample forms (16-bit and 8-bit PCM, Yamaha 4-bit ADPCM) decoded to WAV.

ADPCM is decoded exactly as port/runtime/aica.c's adpcm_step plays it from sound RAM: the low nibble first, each
nibble a signed step of the running quantiser (from 127, clamped to 127..24576), the signal clamped to 16 bits.
Nearest existing: port/runtime/aica.c (the same ADPCM, but in C inside the voice mixer, reading sound RAM a frame
at a time, not files) and port/tools/aica_regs.py (register fields only) - neither reads a bank or writes a WAV.

DTPK (all words little-endian), as read from the banks themselves:
  0x00 "DTPK", 0x08 the bank's size; 0x20.. eight table offsets: [4] the programs, [7] the samples.
  Samples: u32 last index (n - 1), then n entries of 16 bytes:
    u32 offset in the file (bits 0..23) | sample form << 24 (the AICA's PCMS: 0 PCM16, 1 PCM8, 2/3 ADPCM),
    u16 loop start, u16 loop end (in samples), u32 0, u32 size in bytes.
  Programs: a 0x50-byte head (0x10: u16 last record index), then 0x40-byte records: byte 0 the key, byte 1 bit 7
  in use, byte 2 the sample index, byte 10 a signed transpose in semitones against the chip's 44100 Hz (-12 is
  22050 Hz, -24 is 11025 Hz), byte 11 a fine tune. The sample's rate here is 44100 * 2^(transpose / 12) from the
  transpose most of its records give (fine tune left out); a sample no record names gets None.
SPSD: 0x00 "SPSD", 0x08 the form (as PCMS), 0x09 bit 0 stereo, 0x0c the data's size, 0x2a u16 the rate; data from
  0x40; stereo data alternates 0x2000-byte blocks, left first, each channel's ADPCM running on across its blocks.

Usage: python aica_bank.py dump FILE OUTDIR [--offset N] [--size N]   (a DTPK or SPSD file, or one inside an image)
       python aica_bank.py list FILE [--offset N] [--size N]
"""
import argparse
import collections
import json
import os
import struct
import sys
import wave

PCM16, PCM8, ADPCM, ADPCM_STREAM = 0, 1, 2, 3
FORM_NAMES = {PCM16: "pcm16", PCM8: "pcm8", ADPCM: "adpcm", ADPCM_STREAM: "adpcm"}
AICA_RATE = 44100
SPSD_BLOCK = 0x2000
_DIFF = (1, 3, 5, 7, 9, 11, 13, 15)
_SCALE = (0xe6, 0xe6, 0xe6, 0xe6, 0x133, 0x199, 0x200, 0x266)


class AdpcmState:
    """The decoder's running signal and quantiser, as a voice keeps them."""
    def __init__(self):
        self.signal, self.quant = 0, 0x7f


def adpcm_decode(data, frames=None, state=None):
    """Signed 16-bit samples of Yamaha ADPCM bytes (two per byte, low nibble first); frames caps the count."""
    st = state or AdpcmState()
    s, q = st.signal, st.quant
    n = len(data) * 2 if frames is None else min(frames, len(data) * 2)
    out = [0] * n
    k = 0
    for b in data:
        for nib in (b & 15, b >> 4):
            if k == n:
                break
            d = q * _DIFF[nib & 7] // 8
            s = s - d if nib & 8 else s + d
            s = -32768 if s < -32768 else 32767 if s > 32767 else s
            q = q * _SCALE[nib & 7] >> 8
            q = 0x7f if q < 0x7f else 0x6000 if q > 0x6000 else q
            out[k] = s
            k += 1
        if k == n:
            break
    st.signal, st.quant = s, q
    return out


def pcm_decode(data, form, frames=None):
    """Signed 16-bit samples of 16-bit little-endian or signed 8-bit PCM."""
    if form == PCM16:
        n = len(data) // 2 if frames is None else min(frames, len(data) // 2)
        return list(struct.unpack_from("<%dh" % n, data))
    n = len(data) if frames is None else min(frames, len(data))
    return [(b - 256 if b > 127 else b) << 8 for b in data[:n]]


def decode(data, form, frames=None):
    return adpcm_decode(data, frames) if form >= ADPCM else pcm_decode(data, form, frames)


def frames_of(size, form):
    return size * 2 if form >= ADPCM else size // 2 if form == PCM16 else size


def rate_of(transpose):
    return int(round(AICA_RATE * 2.0 ** (transpose / 12.0)))


def is_dtpk(blob):
    return blob[:4] == b"DTPK"


def is_spsd(blob):
    return blob[:4] == b"SPSD"


def dtpk_programs(blob):
    """[(record offset, key, sample index, transpose, fine)] for each program record in use."""
    tables = struct.unpack_from("<8I", blob, 0x20)
    prog, samples = tables[4], tables[7]
    if not prog or prog + 0x50 > len(blob):
        return []
    last = struct.unpack_from("<H", blob, prog + 0x10)[0]
    end = samples if samples > prog else len(blob)
    out = []
    for i in range(last + 1):
        r = prog + 0x50 + 0x40 * i
        if r + 0x40 > end:
            break
        if blob[r + 1] & 0x80:
            out.append((r, blob[r], blob[r + 2], struct.unpack_from("<b", blob, r + 10)[0], blob[r + 11]))
    return out


def dtpk_samples(blob):
    """The bank's samples: [{index, offset, form, size, frames, loop_start, loop_end, rate, transposes}]."""
    if not is_dtpk(blob):
        raise ValueError("not a DTPK bank")
    table = struct.unpack_from("<8I", blob, 0x20)[7]
    if not table or table + 4 > len(blob):
        return []
    last = struct.unpack_from("<I", blob, table)[0]
    uses = collections.defaultdict(collections.Counter)
    for _, _, idx, tr, _ in dtpk_programs(blob):
        uses[idx][tr] += 1
    out = []
    for i in range(last + 1):
        e = table + 4 + 16 * i
        if e + 16 > len(blob):
            break
        word, lsa, lea, _, size = struct.unpack_from("<IHHII", blob, e)
        off, form = word & 0xffffff, (word >> 24) & 3
        size = min(size, max(0, len(blob) - off))
        tr = uses[i].most_common(1)[0][0] if uses[i] else None
        out.append({"index": i, "offset": off, "form": FORM_NAMES[form], "pcms": form, "size": size,
                    "frames": frames_of(size, form), "loop_start": lsa, "loop_end": lea,
                    "rate": rate_of(tr) if tr is not None else None, "transposes": dict(uses[i])})
    return out


def dtpk_sample_pcm(blob, s):
    return decode(blob[s["offset"]:s["offset"] + s["size"]], s["pcms"])


def spsd_info(blob):
    if not is_spsd(blob):
        raise ValueError("not an SPSD stream")
    form, flags = blob[8] & 3, blob[9]
    size = min(struct.unpack_from("<I", blob, 0x0c)[0], len(blob) - 0x40)
    channels = 2 if flags & 1 else 1
    rate = struct.unpack_from("<H", blob, 0x2a)[0]
    return {"form": FORM_NAMES[form], "pcms": form, "channels": channels, "rate": rate, "size": size,
            "frames": frames_of(size // channels, form)}


def spsd_pcm(blob):
    """(info, [channel samples]) of an SPSD stream."""
    info = spsd_info(blob)
    data = blob[0x40:0x40 + info["size"]]
    if info["channels"] == 1:
        return info, [decode(data, info["pcms"])]
    parts = [bytearray(), bytearray()]
    for o in range(0, len(data), SPSD_BLOCK):
        parts[(o // SPSD_BLOCK) & 1] += data[o:o + SPSD_BLOCK]
    if info["pcms"] >= ADPCM:  # each channel's decoder runs on across its blocks
        return info, [adpcm_decode(bytes(p)) for p in parts]
    return info, [pcm_decode(bytes(p), info["pcms"]) for p in parts]


def write_wav(path, rate, channels):
    """Write 16-bit WAV; channels is a list of sample lists (shorter ones padded with silence)."""
    n = max(len(c) for c in channels)
    inter = []
    for i in range(n):
        for c in channels:
            inter.append(c[i] if i < len(c) else 0)
    with wave.open(path, "wb") as w:
        w.setnchannels(len(channels))
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<%dh" % len(inter), *inter))


def dump(blob, outdir, default_rate=22050):
    """Write each sample (DTPK) or the stream (SPSD) as WAV into outdir; returns the listing written beside them."""
    os.makedirs(outdir, exist_ok=True)
    if is_spsd(blob):
        info, chans = spsd_pcm(blob)
        write_wav(os.path.join(outdir, "stream.wav"), info["rate"] or default_rate, chans)
        info["seconds"] = round(info["frames"] / (info["rate"] or default_rate), 3)
        listing = {"kind": "SPSD", "stream": info}
    else:
        rows = []
        for s in dtpk_samples(blob):
            rate = s["rate"] or default_rate
            if s["size"] > 0:
                write_wav(os.path.join(outdir, "%03d.wav" % s["index"]), rate, [dtpk_sample_pcm(blob, s)])
            rows.append(dict(s, seconds=round(s["frames"] / rate, 3), rate_guessed=s["rate"] is None))
        listing = {"kind": "DTPK", "samples": rows}
    with open(os.path.join(outdir, "listing.json"), "w", encoding="utf-8") as f:
        json.dump(listing, f, indent=1)
    return listing


def _read(path, offset, size):
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read(size if size else -1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("dump", "list"))
    ap.add_argument("file")
    ap.add_argument("outdir", nargs="?")
    ap.add_argument("--offset", type=lambda v: int(v, 0), default=0)
    ap.add_argument("--size", type=lambda v: int(v, 0), default=0)
    a = ap.parse_args(argv)
    blob = _read(a.file, a.offset, a.size)
    if a.cmd == "dump":
        if not a.outdir:
            ap.error("dump needs OUTDIR")
        listing = dump(blob, a.outdir)
    else:
        listing = {"kind": "SPSD", "stream": spsd_info(blob)} if is_spsd(blob) else \
            {"kind": "DTPK", "samples": dtpk_samples(blob)}
    json.dump(listing, sys.stdout, indent=1)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
