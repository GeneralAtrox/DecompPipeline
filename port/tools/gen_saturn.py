"""Saturn port generator (game.json "saturn"): turns the translator's C for a game's functions into the files the Saturn
kit (port/saturn) builds with: chunked translation units, the sorted function table, and the game's constants header.

Nearest existing: gen_port.py / tx_port.py - they build the SH-4 (Dreamcast) port from the sp-input judged form and a
registry of readable C; the SH-2 translator has neither yet, so this one takes the judge-form C as it is. The one
change made to the text is the entry stack: the judge form starts each function at a fixed r15 (`r15 = 0x060fe000u`);
here it starts at the sp tc_call gives it (sat_entry_sp), so a function's stack arguments and its pushes sit where the
caller left room. Nothing else in a function is touched, and the stub/verdict state is reported at run time.

  gen_saturn.py --game game.json --tx DIR_OF_ADDR.C --out OUTDIR
"""
import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scene_map  # noqa: E402

CHUNK = 120
DYNAMIC = 0x2000
MARGIN = 64
ENTRY_SP = re.compile(r"\br15\s*=\s*0x060fe000u;")
# the wrapper of a function that calls itself passes the entry sp to its body as a constant: the body must run at the sp
# the caller really gave it (stack-passed arguments are read at r15 + 0x54...), as every other function does
WRAP_SP = re.compile(r"\bbody\(0x060fe000u,")
PUSH = re.compile(r"\br15\s*(-=|\+=)\s*([^;]*);")
OTHER_R15 = re.compile(r"\br15\s*=(?!=)\s*([^;]*);")
COMMENT = re.compile(r"/\*.*?\*/", re.S)
SAFE_R15 = re.compile(r"(?:sat_entry_sp|r\d+(?: [+-] (?:0x[0-9a-f]+|\d+)u)?|rd32\(r15\)|rd32\(0x[0-9a-f]+u\))")
DECL = re.compile(r"\bu32 r0\s*=\s*0(?:,[^;]*)?;")  # the statement: the translator's compact form has it on the function's first line
HELPERS = (
    "static u32 sx8(u32 a){return (u32)(s32)(s8)rd8(a);}",
    "static u32 sx16(u32 a){return (u32)(s32)(s16)rd16(a);}",
)


RETURN = re.compile(r"\breturn\s+(?!tc_call\(|tc_tail(?:_sp)?\()([^;{}]*);")
CALL = re.compile(r"(\br0\s*=\s*tc_call\([^;]*\);)")
TAIL_CALL = re.compile(r"\br0\s*=\s*tc_call\([^;]*\);\s*$")
JUMP = re.compile(r"\br0\s*=\s*tc_call\(([^;]*)\);\s*return r0;")
PARAM = {4: "a0", 5: "a1", 6: "a2", 7: "a3"}


def exit_regs(text, regs):
    """A callee's exit r4..r7, for code that reads one after a call (the SH-2 asm of SGL's slave command loop does:
    its handlers advance r4 over their own arguments). The judge form returns only r0, so a caller kept its own r4;
    here a function hands its r4 on at every return (a shared register, as r8..r14 already are) and its caller
    reloads it after each call. The value is what the CPU would hold, so code that ignores it is unaffected."""
    decl = DECL.search(text)
    if not decl or "static" in text:
        return text
    for n in regs:
        local = re.search(rf"\br{n}\s*=\s*0\b", decl.group(0)) is not None
        if not local and n not in PARAM:
            continue
        out = f"r{n}" if local else PARAM[n]

        def ret(m, n=n, out=out, local=local):
            # a parameter register handed on after a tail call (a fall-through chain such as SGL's shift ladders)
            # keeps what the callee left: this function's own parameter is only partly shifted
            if not local and TAIL_CALL.search(m.string[: m.start()]):
                return "return %s;" % m.group(1)
            return "{ sh4_set_r(%d, %s); return %s; }" % (n, out, m.group(1))

        text = RETURN.sub(ret, text)
        # after every call the register is what the callee left; a parameter register is its `a` variable (SGL's
        # slave handlers add to r4 after a helper has already backed it up)
        text = CALL.sub(lambda m, n=n, out=out: m.group(1) + " %s = sh4_r(%d);" % (out, n), text)
    return text


def frame_bound(text):
    """(bytes, computed): what a body can push below its entry sp: every `r15 -= K` summed (covers any path, never
    clamped - SGL loaders take 12 KB locals), or DYNAMIC and computed=True for a computed move. Plain restores
    (`r15 = r14`, `r15 = rd32(...)`) are the frame being unwound, not new room."""
    code = DECL.sub(" ", COMMENT.sub(" ", text))
    n = 0
    for op, rhs in PUSH.findall(code):
        k = re.fullmatch(r"(0x[0-9a-f]+|\d+)u", rhs.strip())
        if not k:
            return DYNAMIC, True
        v = int(k.group(1), 0)
        if op == "-=":
            n += v
        elif v & 0x80000000:
            n += (1 << 32) - v
    for rhs in OTHER_R15.findall(code):
        if not SAFE_R15.fullmatch(rhs.strip()):
            return DYNAMIC, True
    return n + MARGIN, False


def functions(tx):
    out = {}
    for p in sorted(tx.glob("*.c")):
        if re.fullmatch(r"[0-9a-f]{8}", p.stem):
            out[int(p.stem, 16)] = p
    return out


POP_PR = re.compile(r"\bpr\s*=\s*rd32\(r15\);\s*r15\s*\+=\s*4u;")
LABEL_RETURN = re.compile(r"(tc_call\([^;\n]*;(?:\s*\w+\s*=\s*sh4_r\(\d+\);)*\s*)if\s*\(pr\s*==\s*(0x[0-9a-f]+u)\)\s*goto")


def pr_returns(text):
    """A function that pops pr from its stack returns to its caller's caller, not to the label the caller set pr to
    (SGL jumps into the middle of a handler that way): mark the pop, and have the call site's label test ask."""
    text = POP_PR.sub(lambda m: m.group(0) + " sat_pr_popped();", text)
    return LABEL_RETURN.sub(lambda m: m.group(1) + "sat_window_back(r15); if (pr == " + m.group(2) + " && !sat_pr_took()) goto", text)


def live_in(text):
    """r0..r3 reach a function as the caller held them (a function that never writes r0 hands it back, and SGL's inner
    routines read r1..r3 as inputs): the first declaration line starts them from the call's values, not 0."""
    def fix(m):
        return re.sub(r"\b(r[0-3])\s*=\s*0\b", lambda k: "%s = sat_in_r(%s)" % (k.group(1), k.group(1)[1]), m.group(0), count=4)
    return DECL.sub(fix, text, count=1)


WAIT_READ = r"(rd8\(sh4_stc_gbr\(\) \+ %du\);\s*t = [^;]+;)(\s*\} while \(t\);)"


def vblank_waits(text, offsets):
    """A loop on a byte only the vertical-blank handler sets (game.json "vblank_flags", offsets from GBR) tells the machine it waits."""
    for o in offsets:
        text = re.sub(WAIT_READ % o, lambda m: m.group(1) + " if (t) sat_vblank_wait();" + m.group(2), text)
    return text


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", required=True)
    ap.add_argument("--tx", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--extra", action="append", default=[], help="a dir of more ADDR.c files (functions the catalogue missed)")
    a = ap.parse_args(argv)
    cfg = json.loads(pathlib.Path(a.game).read_text(encoding="utf-8"))["saturn"]
    regs = tuple(cfg.get("exit_regs", (4,)))
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    fns = functions(pathlib.Path(a.tx))
    more = {}
    for extra in a.extra:
        more.update(functions(pathlib.Path(extra)))
    fns = {k: v for k, v in fns.items() if k not in more}
    if not fns:
        sys.exit(f"no ADDR.c files in {a.tx}")
    rows, chunk, idx, extra_texts = [], [], 0, []
    for addr, p in sorted({**fns, **more}.items()):
        text = p.read_text(encoding="utf-8")
        body = ENTRY_SP.sub("r15 = sat_entry_sp;", text, count=1)
        text = body.replace('#include "tc_rt.h"\n', "")
        text = WRAP_SP.sub("body(sat_entry_sp,", text)
        for h in HELPERS:
            text = text.replace(h, "")
        fb, computed = frame_bound(text.replace("r15 = sat_entry_sp;", ""))
        text = JUMP.sub(lambda m: "return tc_tail_sp(%s, r15);" % m.group(1), text)  # a call that ends the function is a jump: it runs in this frame
        text = exit_regs(text, regs)
        text = pr_returns(text)
        text = live_in(text)
        text = vblank_waits(text, cfg.get("vblank_flags", ()))
        if f"0x{addr:08x}" in cfg.get("main_loops", ()):
            text = re.sub(r"(u32 fn_[0-9a-f]+\([^)]*\)\s*\{)", lambda m: m.group(1) + " sat_idle_turn();", text, count=1)
        rows.append((addr, fb, computed))
        if addr in more:
            extra_texts.append(text)
            continue
        chunk.append(text)
        if len(chunk) == CHUNK:
            write_chunk(out, idx, chunk)
            chunk, idx = [], idx + 1
    if chunk:
        write_chunk(out, idx, chunk)
        idx += 1
    write_extra(out, extra_texts)
    table = ['#include "tc_rt.h"', '#include "sat.h"', ""]
    table += [f"u32 fn_{ad:08x}(u32 a0, u32 a1, u32 a2, u32 a3);" for ad, _, _ in rows]
    table += ["", "const SatFn sat_fns[] = {"]
    table += [f"    {{0x{ad:08x}u, fn_{ad:08x}, {fb}u}}," for ad, fb, _ in rows]
    table += ["};", f"const int sat_nfns = {len(rows)};", ""]
    (out / "sat_table.c").write_text("\n".join(table), encoding="utf-8")
    hdr = [
        "/* generated by gen_saturn.py from game.json: this game's constants for the Saturn kit */",
        f"#define SAT_IMAGE_BASE 0x{int(cfg['image_base'], 0):08x}u",
        f"#define SAT_GAME_NAME {json.dumps(cfg.get('name', 'Saturn game'))}",
        f"#define SAT_IMAGE_DEFAULT {json.dumps(cfg['image'])}",
        f"#define SAT_ENTRY_FN 0x{int(cfg['entry'], 0):08x}u",
        f"#define SAT_ENTRY_SP 0x{int(cfg['entry_sp'], 0):08x}u",
        f"#define SAT_ENTRY_GBR 0x{int(cfg.get('entry_gbr', cfg['entry_sp']), 0):08x}u",
        f"#define SAT_FRAME_HZ {int(cfg.get('frame_hz', 50))}u",
        f"#define SAT_NSOUND_CLEAR {len(cfg.get('sound_clear', []))}",
        "static const unsigned sat_sound_clear[] = {" + "".join(f"0x{int(x, 0):x}u, " for x in cfg.get("sound_clear", [])) + "0};",
    ]
    if cfg.get("product"):  # the disc header's product number (console_sat.c refuses another game's disc)
        hdr.append(f"#define SAT_PRODUCT {json.dumps(cfg['product'].ljust(10))}")
    if cfg.get("slave_sp"):  # the slave SH-2's stack top: the game loads it from its own memory, which the translated slave program does not run
        hdr.append(f"#define SAT_SLAVE_SP 0x{int(cfg['slave_sp'], 0):08x}u")
    if cfg.get("sound_cpu"):  # the 68000 runs the game's own sound driver (sat_scsp.c); the stand-ins below are then unused
        hdr.append("#define SAT_SOUND_CPU 1")
    ss = cfg.get("sound_stream")
    if ss:  # the 68K driver's stream-progress report (sat_scsp.c): off when the game names none
        hdr += [
            "#define SAT_SOUND_STREAM 1",
            f"#define SAT_SS_START_ADDR 0x{int(ss['start_addr'], 0):x}u",
            f"#define SAT_SS_START_CMD 0x{int(ss['start_cmd'], 0):x}u",
            f"#define SAT_SS_STATUS_ADDR 0x{int(ss['status_addr'], 0):x}u",
            f"#define SAT_SS_STATUS_N {int(ss['status_count'])}u",
            f"#define SAT_SS_STATUS_STEP {int(ss.get('status_step', 1))}u",
            f"#define SAT_SS_BLOCK {int(ss['block'])}u",
            f"#define SAT_SS_RATE {int(ss['rate'])}u",
        ]
    hdr.append("")
    (out / "sat_game.h").write_text("\n".join(hdr), encoding="utf-8")
    game_dir = pathlib.Path(a.game).resolve().parent  # scene_dump.json sits beside game.json; without it the dump has the kit's fields only
    (out / "scene_map.c").write_text(scene_map.scene_map_c({"dir": game_dir}, "", "", header=""), encoding="utf-8")
    (out / "sat_chunks.txt").write_text("\n".join(f"txchunk_{i:02d}.c" for i in range(idx)) + "\n", encoding="utf-8")
    print(f"{len(rows)} functions, {idx} chunks, {sum(1 for _, _, c in rows if c)} with a computed frame")


def unit(out, name, texts):
    head = '#include "tc_rt.h"\n#include "sat.h"\n' + "\n".join(HELPERS) + "\n"
    (out / name).write_text(head + "\n".join(texts), encoding="utf-8")


def write_extra(out, texts):
    """Functions the catalogue missed (--extra): one unit built beside the kit, so adding one does not rebuild the chunks."""
    unit(out, "txextra.c", texts)


def write_chunk(out, idx, texts):
    unit(out, f"txchunk_{idx:02d}.c", texts)


if __name__ == "__main__":
    main()
