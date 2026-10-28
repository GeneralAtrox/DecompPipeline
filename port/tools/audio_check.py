"""Audio check: the port's sound against Flycast's, from the same input.

    python tools/audio_check.py emu --game DIR --out DIR --input START@900,.. --seconds 500 [--flycast EXE]
    python tools/audio_check.py compare --emu DIR --port FILE.wav [--report FILE]

`emu` plays the game in Flycast from boot with the lockstep script (tools/lockstep_emu.lua, so OUT/emu.txt is also a
lockstep recording) and tools/audio_emu.lua, which writes the chip's output sample by sample (OUT/audio_emu.txt: the
voices' direct sum, the DSP's effect return and the CD, each left and right) and every key on with the voice's
registers (OUT/audio_keys.txt). `compare` lines the port's --audio-dump up with it (the CD contribution, the same disc
data on both sides, fixes the offset) and measures each sound effect: from each key on (Flycast's) to the next key on
of that voice or 2 s, the RMS envelope of the effects (voices and DSP, the CD taken out) in 10 ms steps on both sides,
and their error in dB. It also reports the voice settings the driver made (aica_regs.py) and the DSP's share.
With --pair sound (a game whose own driver picks the voices, runtime/aica_arm.c) a port key on pairs with the
emulator's next key on of the same sound (the voice's step and loop end), on whatever voice each side gave it, so a
voice the port does not key (the driver's ADX stream voices, which the kit's adx_mw plays) shifts nothing.
"""
import argparse
import pathlib
import sys
import wave

import numpy as np

import lockstep as L
from aica_regs import decode as decode_regs, fields as reg_fields

TOOLS = pathlib.Path(__file__).resolve().parent
RATE = 44100
STEP = 441              # 10 ms envelope steps
FLOOR_DB = -60.0        # envelope floor relative to full scale (32768)


def emu(a):
    game = pathlib.Path(a.game).resolve()
    out = pathlib.Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    spec = pathlib.Path(a.spec or game / "lockstep.spec").resolve()
    lua = {"AU_OUT": '"%s"' % (out / "audio_emu.txt").as_posix(), "AU_KEYS": '"%s"' % (out / "audio_keys.txt").as_posix()}
    wall = L.emu_run(game, spec, out / "emu.txt", a.seconds, L.parse_input(a.input), a.flycast, lua_vars=lua,
                     extra_lua=[TOOLS / "audio_emu.lua"])
    print(f"emulator: {wall:.0f} s; {out / 'audio_emu.txt'}")
    return 0


def read_emu(path):
    """(frames x 6 int32: dry l r, dsp l r, cd l r) on the chip's sample clock from the first ordinal, gaps zero; read
    in blocks (the text runs to hundreds of MB) and kept as PATH.npy for the next compare."""
    cache = pathlib.Path(str(path) + ".npy")
    if cache.exists() and cache.stat().st_mtime >= pathlib.Path(path).stat().st_mtime:
        arr = np.load(cache)
        return arr[1:], int(arr[0, 0])
    blocks, first, at, lines = [], None, 0, []

    def put():
        nonlocal at
        if lines:
            blocks.append(np.array(" ".join(lines).split(), dtype=np.int32).reshape(-1, 6))
            at += len(blocks[-1])
            lines.clear()

    with open(path) as f:
        for line in f:
            if line[0] == "@":
                put()
                o = int(line.split()[1])
                first = o if first is None else first
                if o - first > at:
                    blocks.append(np.zeros((o - first - at, 6), np.int32))
                    at = o - first
            else:
                lines.append(line)
                if len(lines) >= 100000:
                    put()
    put()
    arr = np.concatenate([np.array([[first or 0, 0, 0, 0, 0, 0]], np.int32)] + blocks)
    np.save(cache, arr)
    return arr[1:], first or 0


def read_wav(path):
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, 2).astype(np.int32)


def envelope(x):
    """RMS of each 10 ms step of a mono-summed signal, in dB of full scale (floored)."""
    n = len(x) // STEP * STEP
    m = x[:n].reshape(-1, STEP, 2).astype(np.float64)
    rms = np.sqrt((m ** 2).mean(axis=(1, 2)))
    return np.maximum(20 * np.log10(np.maximum(rms, 1e-9) / 32768.0), FLOOR_DB)


def read_keys(path):
    keys, dsp, stats = [], [], ""
    for line in pathlib.Path(path).read_text().splitlines():
        f = line.split()
        if f[0] in "KF":
            keys.append((f[0], int(f[1]), int(f[2]), int(f[3]), f[4]))
        elif f[0] == "D":
            dsp.append((int(f[1]), f[2]))
        elif f[0] == "S":
            stats = line
    return keys, dsp, stats


def read_port_keys(path, kind="K"):
    """[(frame, voice, level, dipan, step, frames)] from the port's --audio-keys log: its key ons (kind K) or key
    offs (F)."""
    return [tuple(int(x) for x in line.split()[1:]) for line in pathlib.Path(path).read_text().splitlines()
            if line.startswith(kind + " ")]


def stop_shifts(keys, first, pkeys, poffs):
    """[(emu frame, voice, shift)]: each voice's n-th key off on both sides, the port's minus the emulator's, each
    measured from that voice's last paired key on (so the two timelines' gaps cancel), in output frames."""
    emu_ev, port_ev = {}, {}
    for kind, _, o, ch, _ in keys:
        if o >= first:
            emu_ev.setdefault(ch, []).append((o - first, kind))
    for k in pkeys:
        port_ev.setdefault(k[1], []).append((k[0], "K"))
    for k in poffs:
        port_ev.setdefault(k[1], []).append((k[0], "F"))
    out = []
    for ch, ev in emu_ev.items():
        pev = sorted(port_ev.get(ch, []))
        eon = [t for t, k in ev if k == "K"]
        pon = [t for t, k in pev if k == "K"]
        for n, (e, p) in enumerate(zip([t for t, k in ev if k == "F"], [t for t, k in pev if k == "F"])):
            ie = sum(1 for t in eon if t <= e)
            ip = sum(1 for t in pon if t <= p)
            if ie and ie == ip:
                out.append((e, ch, (p - pon[ip - 1]) - (e - eon[ie - 1])))
    return sorted(out)


def pairs(keys, first, pkeys):
    """[(emu frame, port frame, voice, regs, port key)]: the n-th key on of each voice on both sides (port n is the
    driver's voice n), so each effect is measured at its own start whatever the two timelines do between them (the
    port does not wait for the disc as the console does)."""
    emu_on, port_on = {}, {}
    for kind, _, o, ch, regs in keys:
        if kind == "K" and o >= first:
            emu_on.setdefault(ch, []).append((o - first, regs))
    for k in pkeys:
        port_on.setdefault(k[1], []).append(k)
    out = []
    for ch, ons in emu_on.items():
        for (e, regs), pk in zip(ons, port_on.get(ch, [])):
            out.append((e, pk[0], ch, regs, pk))
    return sorted(out)


def voice_step(f):
    """A key on's step (16.16) from its OCT and FNS, as runtime/aica_chan.c sets the voice's."""
    base = (1024 + f["FNS"]) << 6
    return base >> (16 - f["OCT"]) if f["OCT"] & 8 else base << f["OCT"]


def sound_pairs(keys, first, pkeys, poffs):
    """(pairs, stop shifts, unpaired port key ons) as pairs() and stop_shifts() give them, but each port key on paired
    with the emulator's next key on of the same sound (step and loop end) after that sound's last pair (each sound in
    its own order: two sounds keyed close together may come in either order); each pair's key off is the voice's next
    one on each side before that voice's next key on."""
    emu = [(o - first, ch, regs, kind) for kind, _, o, ch, regs in keys if o >= first]
    eon = {}
    for t, ch, regs, kind in emu:
        if kind == "K":
            f = reg_fields(regs)
            eon.setdefault((voice_step(f), f["LEA"] or 1), []).append((t, ch, regs))
    out, shifts, unpaired, used = [], [], 0, {}
    for pk in sorted(pkeys):
        want = (pk[4], pk[5])
        if used.get(want, 0) >= len(eon.get(want, [])):
            unpaired += 1
            continue
        e, ch, regs = eon[want][used.get(want, 0)]
        used[want] = used.get(want, 0) + 1
        out.append((e, pk[0], ch, regs, pk))
        e_next = next((t for t, c, _, k in emu if c == ch and t > e and k == "K"), None)
        e_off = next((t for t, c, _, k in emu if c == ch and t > e and k == "F"), None)
        p_next = next((k[0] for k in sorted(pkeys) if k[1] == pk[1] and k[0] > pk[0]), None)
        p_off = next((k[0] for k in sorted(poffs) if k[1] == pk[1] and k[0] > pk[0]), None)
        if e_off is not None and p_off is not None and (e_next is None or e_off < e_next) and \
                (p_next is None or p_off < p_next):
            shifts.append((e, ch, (p_off - pk[0]) - (e_off - e)))
    return sorted(out), sorted(shifts), unpaired


def compare(a):
    edir = pathlib.Path(a.emu)
    em, first = read_emu(edir / "audio_emu.txt")
    keys, dsp, stats = read_keys(edir / "audio_keys.txt")
    port = read_wav(a.port)
    pkeys = read_port_keys(a.keys)
    fx = em[:, 0:2] + em[:, 2:4]
    nk = sum(1 for k in keys if k[0] == "K")
    lines = [f"emulator {len(em)} frames, {nk} key ons ({stats or 'no stats'}); port {len(port)} frames, "
             f"{len(pkeys)} key ons"]
    dsp_share = np.sqrt((em[:, 2:4].astype(np.float64) ** 2).sum() / max((fx.astype(np.float64) ** 2).sum(), 1))
    lines.append(f"DSP effect return: {100 * dsp_share:.2f}% of the effects' RMS")
    rows = []
    poffs = read_port_keys(a.keys, "F")
    if a.pair == "sound":
        paired, shifts, unpaired = sound_pairs(keys, first, pkeys, poffs)
        lines.append(f"paired by sound: {len(paired)} of the port's {len(pkeys)} key ons ({unpaired} without a match)")
    else:
        paired, shifts = pairs(keys, first, pkeys), stop_shifts(keys, first, pkeys, poffs)
    starts = sorted(e for e, *_ in paired)
    for e, p, ch, regs, pk in paired:
        nxt = next((t for t in starts if t > e), len(fx))
        n = min(2 * RATE, len(fx) - e, len(port) - p, max(nxt - e, 0))
        if n < STEP * 3:
            continue
        ee, pe = envelope(fx[e:e + n]), envelope(port[p:p + n])
        audible = ee > FLOOR_DB + 20
        if not audible.any():
            continue
        diff = (pe - ee)[audible]
        rows.append((e, ch, float(np.abs(diff).mean()), float(diff.mean()), float(np.abs(diff).max()), regs, pk, n))
    late = [x for x in shifts if abs(x[2]) > STEP * 3]
    lines.append(f"key offs: {len(shifts)} paired, {len(shifts) - len(late)} within 30 ms of the emulator's (from the "
                 f"voice's start); apart: " + (", ".join(f"t={e / RATE:.3f}s ch{ch} {d / RATE * 1000:+.0f} ms"
                                                      for e, ch, d in late[:12]) or "none"))
    lines.append(f"{len(rows)} effects measured (paired key ons, audible, up to the next key on or 2 s)")
    if rows:
        errs = [r[2] for r in rows]
        lines.append(f"  envelope error |dB|: mean {np.mean(errs):.2f}, median {np.median(errs):.2f}, "
                     f"90th percentile {np.percentile(errs, 90):.2f}; level bias (port - emu) mean "
                     f"{np.mean([r[3] for r in rows]):+.2f} dB")
    for e, ch, err, bias, mx, regs, pk, n in rows[: a.limit]:
        lines.append(f"  t={e / RATE:8.3f}s ch{ch:2d} {n / RATE:5.2f}s err {err:5.2f} bias {bias:+6.2f} max {mx:5.1f}"
                     f" | port lvl {pk[2]} pan {pk[3]} | {decode_regs(regs, ('PCMS', 'LPCTL', 'DISDL', 'DIPAN', 'OCT', 'FNS', 'LSA', 'LEA'))}")
    text = "\n".join(lines)
    print(text)
    if a.report:
        pathlib.Path(a.report).write_text(text + "\n")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["emu", "compare"])
    ap.add_argument("--game")
    ap.add_argument("--spec")
    ap.add_argument("--out")
    ap.add_argument("--input", default="")
    ap.add_argument("--seconds", type=int, default=300)
    ap.add_argument("--flycast")
    ap.add_argument("--emu")
    ap.add_argument("--port")
    ap.add_argument("--keys", help="the port's --audio-keys log")
    ap.add_argument("--pair", choices=["voice", "sound"], default="voice",
                    help="pair key ons by voice (the default) or by sound (a game's own driver picks the voices)")
    ap.add_argument("--report")
    ap.add_argument("--limit", type=int, default=60)
    a = ap.parse_args(argv)
    return emu(a) if a.step == "emu" else compare(a)


if __name__ == "__main__":
    sys.exit(main())
