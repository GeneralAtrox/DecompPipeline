"""Saturn port generator (game.json "saturn"): turns the translator's C for a game's functions into the files the Saturn
kit (port/saturn) builds with: chunked translation units, the sorted function table, and the game's constants header.

Nearest existing: gen_port.py / tx_port.py - they build the SH-4 (Dreamcast) port from the sp-input judged form and a
registry of readable C; the SH-2 translator has neither yet, so this one takes the judge-form C as it is. The one
change made to the text is the entry stack: the judge form starts each function at a fixed r15 (`r15 = 0x060fe000u`);
here it starts at the sp tc_call gives it (sat_entry_sp), so a function's stack arguments and its pushes sit where the
caller left room, and each call passes the caller's r15 (tc_call_sp), so a callee that takes more stack words than a
TcArgs holds reads the rest from the caller's stack. Nothing else in a function is touched, and the stub/verdict state is
reported at run time.

  gen_saturn.py --game game.json --tx DIR_OF_ADDR.C --out OUTDIR
"""
import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scene_map  # noqa: E402
import gen_saturn_menus  # noqa: E402
import gen_saturn_level  # noqa: E402
import gen_saturn_heads  # noqa: E402

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
CALL_SP = re.compile(r"\btc_call\(([^;]*?), &a\)")  # a call from a frame whose r15 is the real sp: the callee's 9th+ stack words
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
        if "r15 = sat_entry_sp;" in text and "body(" not in text:
            text = CALL_SP.sub(r"tc_call_sp(\1, &a, r15)", text)
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
    ws = cfg.get("widescreen")
    if ws:  # the picture composed wider in play runs (sat_wide.c); "rbg0": only pictures that show RBG0 (3D), not 2D screens
        if ws not in (True, "rbg0"):
            sys.exit(f"game.json widescreen: {ws!r} is not true or \"rbg0\"")
        hdr.append(f"#define SAT_WIDESCREEN {1 if ws is True else 2}")
    wc = cfg.get("wide_cull")
    if wc:  # SGL's work area and the functions that read its window: the culling widened with the picture (sat_wide.c)
        hdr += [f"#define SAT_WIDE_SGL 0x{int(wc['sgl'], 0):08x}u",
                "#define SAT_WIDE_CULL_AT {" + ", ".join(f"0x{int(x, 0):08x}u" for x in wc["at"]) + "}"]
    gs = cfg.get("ground_shadows")
    if gs:  # the game's floor-shadow quads, drawn before the rest of VDP1's list in play runs (sat_vdp1_list.c)
        hdr += [f"#define SAT_GROUND_SHADOW_PMOD 0x{int(gs['pmod'], 0):04x}u", f"#define SAT_GROUND_SHADOW_SRCA 0x{int(gs['srca'], 0):04x}u"]
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
    hdr += twin_stick_h(cfg.get("twin_stick"))
    hdr += auto_load_h(cfg.get("auto_load"))
    hdr += movie_h(cfg.get("movie"))
    hdr += netplay_h(cfg.get("netplay"))
    hdr += team_fight_h(cfg.get("team_fight"))
    hdr += gen_saturn_level.level_edit_h(cfg.get("level_edit"), pathlib.Path(a.game).resolve().parent, step_c)
    hdr += gen_saturn_menus.menus_h(cfg)  # menus and menu_panel
    hdr += gen_saturn_heads.heads_h(cfg)  # new head types (sat_heads.c)
    hdr.append("")
    (out / "sat_game.h").write_text("\n".join(hdr), encoding="utf-8")
    (out / "sat_part_data.h").write_text(gen_saturn_heads.part_choice_h(cfg.get("part_choice")), encoding="utf-8")
    game_dir = pathlib.Path(a.game).resolve().parent  # scene_dump.json sits beside game.json; without it the dump has the kit's fields only
    (out / "scene_map.c").write_text(scene_map.scene_map_c({"dir": game_dir}, "", "", header=""), encoding="utf-8")
    (out / "sat_chunks.txt").write_text("\n".join(f"txchunk_{i:02d}.c" for i in range(idx)) + "\n", encoding="utf-8")
    print(f"{len(rows)} functions, {idx} chunks, {sum(1 for _, _, c in rows if c)} with a computed frame")


def twin_stick_h(ts):
    """game.json twin_stick, the right stick turning in the game's fights (port/base/pad_twin.c), as the PortTwinStick
    sat_game.h gives: {"fight": a function the game runs only in a fight, "turn": [left, right], "strafe": [left, right]},
    the buttons by the console's action names."""
    if not ts:
        return []
    names = [json.dumps(n) for n in list(ts["turn"]) + list(ts["strafe"])]
    if len(names) != 4:
        sys.exit("gen_saturn: twin_stick turn and strafe each take two buttons, left then right")
    return [f"#define SAT_TWIN_STICK {{0x{int(ts['fight'], 0):08x}u, {{{names[0]}, {names[1]}}}, {{{names[2]}, {names[3]}}}}}"]
def netplay_h(np):
    """game.json netplay, online play for two (port/saturn/sat_net.c): {"pad": the game's pad the other player takes (1, the
    second, is the only one so far), "delay": frames of input delay (default 3), "hash_every": frames between desync checks
    (default 50), "port": the UDP port a host waits on (default 29720), "test_byte": a work-RAM address --net-test-desync
    flips}. Absent: the build has no online play."""
    if not np:
        return []
    if int(np.get("pad", 1)) != 1:
        sys.exit("gen_saturn: netplay pad: only 1 (the second pad) is supported")
    return ["#define SAT_NETPLAY 1", f"#define SAT_NET_DELAY {int(np.get('delay', 3))}",
            f"#define SAT_NET_HASH_EVERY {int(np.get('hash_every', 50))}", f"#define SAT_NET_PORT {int(np.get('port', 29720))}",
            f"#define SAT_NET_TEST_BYTE 0x{int(str(np.get('test_byte', '0')), 0):08x}u"] + gen_saturn_menus.net_menu_h(np)


def team_fight_h(tf):
    """game.json team_fight, a four-robot fight played two against two (port/saturn/sat_team.c), as the SAT_TEAM_FIGHT
    initialiser: the game's addresses and sizes in the order sat_team.c's TeamGame lists them. Keys: count (the robots in
    the fight, a word) and mode (the four-robot flag, a byte); flags [address, step, CPU byte, out byte] (each robot's
    flag bytes); pos [address, step] (x, y, z, 16.16); energy [address, robot step, full]; the functions control,
    pad_read, brain, setup, camera, hud, frames and sides [two]; gate [count address, after, or-flag address] (when the
    game takes a pad at all); bars {map, line, first_line, cells, table, cell_add, max, tables [side A, side B]}; ringout
    [the function that tests for robots out of the ring, the flag a ring-out sets to end the fight]; names {setup (the
    fight's HUD set-up, which draws two names), draw (the name routine: name, x, y, 0, a fifth argument 1), records (each
    robot's record number, halfwords), name (the first record's name), record (a record's size), map (the text map), x,
    len (the longest name)}; shared_camera {view (the per-view camera, view number), mode (each view's camera mode,
    halfwords), pair (the mode that frames robots 0 and 1 side-on), span (its two points apart, 16ths of the group's radius),
    warning (the ring-edge sign for a view's robot, view number), height (the game's camera height, 16.16, larger is
    higher), lift (added to it while the shared camera runs, 16ths of the camera's distance), near (its least half span,
    whole units)}."""
    if not tf:
        return []
    def a(v):
        return f"0x{int(str(v), 0):08x}u"
    fl, pos, en, gate, b, nm = tf["flags"], tf["pos"], tf["energy"], tf["gate"], tf["bars"], tf["names"]
    sc = tf["shared_camera"]
    if len(tf["sides"]) != 2 or len(b["tables"]) != 2:
        sys.exit("gen_saturn: team_fight sides and bars tables each take two")
    vals = [tf["count"], tf["mode"], fl[0], fl[1], fl[2], fl[3], pos[0], pos[1], en[0], en[1], en[2], tf["control"], tf["pad_read"],
            gate[0], gate[1], gate[2], tf["brain"], tf["setup"], tf["camera"], tf["hud"], tf["frames"], tf["sides"][0], tf["sides"][1],
            b["map"], b["line"], b["first_line"], b["cells"], b["table"], b["cell_add"], b["max"], b["tables"][0], b["tables"][1], tf["ringout"][0], tf["ringout"][1],
            nm["setup"], nm["draw"], nm["records"], nm["name"], nm["record"], nm["map"], nm["x"], nm["len"],
            sc["view"], sc["mode"], sc["pair"], sc["span"], sc["warning"], sc["height"], sc["lift"], sc["near"]]
    return ["#define SAT_TEAM_FIGHT {" + ", ".join(a(v) for v in vals[:21]) + ", {" + a(vals[21]) + ", " + a(vals[22]) + "}, "
            + ", ".join(a(v) for v in vals[23:30]) + ", {" + a(vals[30]) + ", " + a(vals[31]) + "}, " + ", ".join(a(v) for v in vals[32:]) + "}"]


def movie_h(mv):
    """game.json movie, the native movie player (port/saturn/sat_movie.c): {"call": the game's movie routine, "name_arg": the
    argument (0..3) holding the file's name, "skip_after_arg": the one holding how many pictures pass before START skips
    (absent: at once), "skipped": what the routine returns when skipped (0 when played out), "screen": [w, h] the screen
    the picture is centred in (absent: the last composed picture's), "open_blanks": the original's blanks from the call to
    the file's first read, "close_blanks": from the picture that took a skip to the return, "reset_call": the game's routine
    the original calls on A+B+C+START at that picture}."""
    if not mv:
        return []
    out = [f"#define SAT_MOVIE_FN 0x{int(mv['call'], 0):08x}u", f"#define SAT_MOVIE_NAME_ARG {int(mv.get('name_arg', 0))}",
           f"#define SAT_MOVIE_SKIPPED 0x{int(mv.get('skipped', -1)) & 0xffffffff:08x}u"]
    if "skip_after_arg" in mv:
        out.append(f"#define SAT_MOVIE_SKIP_AFTER_ARG {int(mv['skip_after_arg'])}")
    if "screen" in mv:
        out += [f"#define SAT_MOVIE_SCREEN_W {int(mv['screen'][0])}u", f"#define SAT_MOVIE_SCREEN_H {int(mv['screen'][1])}u"]
    for key in ("open_blanks", "close_blanks"):
        if key in mv:
            out.append(f"#define SAT_MOVIE_{key.upper()} {int(mv[key])}u")
    if "reset_call" in mv:
        out.append(f"#define SAT_MOVIE_RESET_CALL 0x{int(mv['reset_call'], 0):08x}u")
    return out + gen_saturn_menus.logos_h(mv)


def step_c(st):
    """one AutoLoadStep initialiser (port/base/autoload.h) from {"enter" or "leave", "press", "poke", "call"}"""
    fn, leave = (st["leave"], 1) if "leave" in st else (st["enter"], 0)
    poke = st.get("poke", [0, 0])
    press = json.dumps(st["press"]) if st.get("press") else "NULL"
    call = st.get("call", [0, 0])
    return (f"{{0x{int(fn, 0):08x}u, {leave}, {press}, 0x{int(str(poke[0]), 0):08x}u, {int(str(poke[1]), 0)}u, "
            f"0x{int(str(call[0]), 0):08x}u, {int(str(call[1]), 0)}u}}")


def auto_load_h(al):
    """game.json auto_load, the game's own load path driven at boot when its save file is there (port/base/autoload.c,
    port/saturn/sat_bup.c), as the defines sat_game.h gives: the file's 11-character name and one AutoLoadStep per step,
    {"enter" or "leave": function, "press": button or "UP+A", "poke": [address, 16-bit value], "call": [function,
    argument]}: a step with call runs the game's own function once when it ends (its poke is set only for the call)."""
    if not al:
        return []
    name = al["file"]
    if len(name) != 11:
        sys.exit(f"gen_saturn: auto_load file {name!r} is not the 11 characters a backup file name has")
    steps = [step_c(st) for st in al["steps"]]
    return [f"#define SAT_AUTO_LOAD_FILE {json.dumps(name)}", "#define SAT_AUTO_LOAD_STEPS {" + ", ".join(steps) + "}"]


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
