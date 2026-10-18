"""One Haiku attempt: build a sealed sandbox, run a locked-down headless session, collect the transcript."""
import hashlib
import json
import os
import re
import shutil
import stat

from . import asm_pool, check, ghidra, paths
from .agent_cli import OutputCap, agent_env, claude_command, readable_effort  # noqa: F401

SANDBOX_FILES = ("TASK.md", "context.md", "tc_rt.h", "check.py")  # plus the packet files (asm*.txt, ghidra*.c)

CHECK_LAUNCHER = """# Runs the dev test set on work.c. The official verdict (dev + hidden) is computed after your session.
import subprocess, sys
sys.exit(subprocess.call([sys.executable, r"{tool}", "--project", r"{project}", "check", "{addr}", "--agent",
                          "--work", r"{work}"]))
"""

TASK = """# TASK: decompile fn_{addr} into C that behaves exactly like the original SH-4 code

A machine judges your work: explanations and claims are ignored, only the official check's PASS/FAIL counts.
Working directory: {box}

## Goal

Write ONE C function in `work.c`:

    {signature}

For every test input it must produce exactly the same observable effects as the original
machine code at 0x{addr}, compared bit for bit:

1. the same return value ({ret_reg}),
2. the same final bytes at every guest memory address the original writes,
3. the same calls: same targets, same order, same argument registers,
4. the same back-bank registers xf0-xf15 (XMTRX),
5. the same fr12-fr15 and r8-r14 at exit, when context.md lists them as runtime state, and the whole FPU
   (fr0-fr15, the FPSCR mode bits) at exit and at every call, when context.md says the whole FPU is state.

## Files in this directory

- `asm.txt`: the original disassembly. This is the authority. Literal-pool constants are annotated (a `mova` line shows the first words at its
  target; read further words of a table with rd32/rdf32 of their address).
  A long function is split into `asm_1.txt`, `asm_2.txt`, ... (at most 400 lines each). Read every part, in order.
- `ghidra.c` (or `ghidra_1.c`, `ghidra_2.c`, ...): Ghidra's decompiler output. It is only a guide and it can be wrong. When it disagrees with asm.txt, follow asm.txt.
- `context.md`: parameter registers, callees, globals, and test coverage.
- `tc_rt.h`: the runtime API. It is the only header you may use.
- `work.c`: your file, and the only file you may change.

Bash runs one command only, exactly `python check.py` (no cd, flags, pipes or redirection; anything else is refused).
It runs the dev test set, prints DEV PASS or DEV FAIL with the differences, and always exits 0.

## Rules

Every rule is enforced automatically. Breaking one fails the attempt.

- R1. Edit only work.c. Do not create, rename or delete any file.
- R2. work.c contains `#include "tc_rt.h"` once and no other preprocessor lines (#define, #if, #pragma, other #include).
- R3. Define exactly one non-static function, fn_{addr}, with exactly the signature above. Every helper function must be `static`.
- R4. All guest memory goes through rd8/rd16/rd32/rdf32 and wr8/wr16/wr32/wrf32. Guest pointers are u32 values, never C pointers.
- R5. Make every call the original makes with tc_call or tc_callf: every jsr, every bsr, and every jmp or bra to another function. Use the same target, the same order and the same argument registers. Never implement a callee and never skip a call.
- R6. No global or static variables, no static locals, no string literals, no initializer list with more than 32 elements, no inline assembly, no extern, no volatile.
- R7. Never special-case test inputs. After your session, a hidden test set with different random inputs is run. Code that only fits the visible tests fails there.
- R8. Never read machine-code bytes, and never interpret or emulate instructions. Reading any instruction address fails the check.
- R9. Floats: use f32 only, never double. Write one C operation per original float instruction, in the original order. Use sh4_ftrc for ftrc, and sh4_fsqrt_rm, sh4_fsrra, sh4_fsca, sh4_fipr and sh4_ftrv for those instructions (sh4_fsqrt_rm rounds as the game's FPSCR does: never write your own square root). When context.md says the whole FPU is state, also sh4_frchg, sh4_fschg, sh4_fpchg, sh4_ftrv_fv and sh4_fipr_fv.
- R10. Never try to read hidden tests, expected outputs or files outside this directory. The file tools are confined to this directory, and every attempt is logged.
- R11. work.c must build with zero compiler warnings (MSVC /W4; every warning is an error). Typical fixes: an explicit cast for a narrowing conversion such as `(u8)`, `(u16)` or `(s32)`; `(void)param_2;` for a parameter the original never reads; removing unused locals.
- R12. When the original passes an address in its own stack frame (`mov r15,rN`, maybe `add #imm,rN`, before the call), pass that same frame address (r15 at that point plus the offset), never 0: the callee reads or fills the local there.

## SH-4 reminders

- An instruction whose mnemonic starts with `_` is in a delay slot. It executes BEFORE the branch above it takes effect, and that includes jsr, bsr, rts, bt/s and bf/s.
- bt and bf test the T bit set by the last cmp/xx, tst or dt. bt/s and bf/s have a delay slot. Plain bt and bf do not.
- mov.b and mov.w loads sign-extend into the 32-bit register. extu.b and extu.w zero-extend.
- `mov.l @(disp,rN)` shows the byte offset already scaled. `mov.l 0x8c...,rN` loads a literal-pool constant, and its value is in the annotation.
- Registers are 32-bit, so use u32 or s32 arithmetic with wraparound. shad and shld shift left for a positive count and right for a negative count.
- The return value is in r0 for integers and fr0 for floats. Arguments are in r4-r7 and fr4-fr11.
- r15 (the stack pointer) is 0x{entry_sp:08x} at entry. The original's stack frame is guest memory just below it: an
  address the original computes from r15 (a local passed to a callee, a stack argument) is that guest address.
- The FPU runs with FPSCR.DN=1: a denormal float operand counts as a zero of the same sign, and a denormal result becomes a signed zero. Plain C float arithmetic and the sh4_ helpers already behave this way here, and so does the rounding mode (FPSCR.RM); a helper that does arithmetic on float bits must treat a denormal input as zero itself. fabs, fneg and plain moves (fmov, flds, fsts, fldi0/1) are not arithmetic: they only touch bits, so a denormal stays a denormal (fabs clears the sign bit, fneg flips it, nothing else changes).
- `pref @rN` is only a cache hint: it has no effect, so skip it. `movca.l r0,@rN` is a plain 32-bit store of r0 (wr32).

## Procedure

1. Read context.md, then every asm part and ghidra part listed in context.md.
2. Write fn_{addr} in work.c by translating asm.txt block by block. Keep the original control flow and the original order of memory writes and calls.
3. Run `python check.py`.
4. If it prints DEV FAIL, read the listed differences, fix the cause in work.c, and run it again.
5. Stop as soon as it prints DEV PASS, or after you have run check.py 12 times.
6. Your final message must be exactly one line: `RESULT: PASS`, or `RESULT: FAIL <one sentence naming the remaining difference>`.
{previous}"""

PREVIOUS = """
## Previous attempt (#{n}), which FAILED

work.c already contains that attempt's code. Start from it and fix it. Do not rewrite it blindly.
Official result: {summary}
{details}
"""


def ret_reg(meta):
    if meta.get("ret_reg"):
        return f"{meta['ret_reg']}, not r0"
    if meta["ret"] == "u64":
        return "r0 (low word) and r1 (high word): return a u64"
    return {"void": "none: the function returns void", "f32": "fr0"}.get(meta["ret"], "r0")


def entry_sp():
    """r15 at entry, from the target's calling convention (target.json abi.entry_sp)."""
    sp = (paths.config().get("abi") or {}).get("entry_sp")
    return int(str(sp), 16) if sp else check.SP


def abi_notes(meta):
    """context.md notes for registers outside the C calling convention (parameters in r0..r3, r8..r14 state,
    callees taking those registers)."""
    lines = []
    low = [p for p in meta["params"] if p["storage"] in ("r0", "r1", "r2", "r3")]
    if low:
        lines += ["", "The original reads " + ", ".join(p["storage"] for p in low) + " before writing them: hand-written "
                  "or compiler-helper code taking", "inputs outside r4-r7. They are ordinary parameters of fn_"
                  f"{meta['addr']} here (the signature lists them); use them as given."]
    if meta.get("ret_reg"):
        lines += ["", f"The result is left in {meta['ret_reg']} (a nonstandard result register): fn_{meta['addr']} returns "
                  f"that value. r0 is not compared."]
    state = meta.get("r_state") or []
    if state:
        lines += ["", "## Runtime state: " + ", ".join(state), "",
                  f"The original reads {', '.join(state)} without saving them first: values its callers keep there.",
                  "They are not parameters: each test sets them at entry, and they are compared at exit. Read their",
                  "entry values with sh4_r(N) and write them with sh4_set_r(N, value) wherever the original writes them",
                  "(N = 8..14). A register the original only saves and restores keeps its entry value: leave it alone."]
    live = meta.get("r_live") or {}
    if live:
        who = "; ".join(f"fn_{t}: " + ", ".join(f"{r} {d}" for r, d in regs.items()) for t, regs in live.items())
        lines += ["", "## Callees that change runtime state", "",
                  f"These callees leave a register changed (their own code advances it): {who} (\"?\": varies).",
                  "tc_call applies that change, as the original's callee does. So after each such call the register",
                  "holds a new value: read sh4_r(N) again after the call wherever the original uses it, and never keep",
                  "a copy across the call or set it back with sh4_set_r before the next call."]
    sq = meta.get("sq_window")
    if sq:
        lines += ["", "## The store-queue window", "",
                  f"{sq} may point into the SH-4 store-queue window (0xe0000000..0xe3ffffff), which the runner maps",
                  "onto RAM as the port does: a store there lands in RAM at once, and pref does nothing in C. One",
                  "effect is then yours to write: where the original's `pref @a` sends a 32-byte slot it never stored",
                  "to (`pref @r14; add #0x20,r14`), the store queue still holds the block it sent 0x40 bytes earlier,",
                  "so copy those 32 bytes from a - 0x40 to a (word by word with rd32/wr32) before moving on."]
    return lines


FPU_STATE = [
    "", "## The whole FPU is state", "",
    "The original switches FPU modes (frchg, fschg or fpchg), so it is tested on the FPU registers themselves, not on",
    "float parameters or a float result:",
    "- Each test sets fr0-fr15 and XMTRX (xf0-xf15) at entry; FPSCR.FR, SZ and PR start at 0. Read the entry values",
    "  with sh4_fr(N) and sh4_xf(N) (N = 0..15).",
    "- fr0-fr15, xf0-xf15 and the three mode bits are compared at exit and at every tc_call (args.fr is not used: the",
    "  callee sees the registers). Write a register with sh4_set_fr(N, v) or sh4_set_xf(N, v) where the original",
    "  writes it; a register the original never writes keeps its value by itself.",
    "- frchg is sh4_frchg() (it swaps fr0-fr15 with xf0-xf15: afterwards sh4_fr(N) is the old xfN), fschg is",
    "  sh4_fschg(), fpchg is sh4_fpchg(). Call them where the original executes them (a delay-slot frchg before a",
    "  call goes before tc_call). ftrv xmtrx,fvN is sh4_ftrv_fv(N); fipr fvM,fvN is sh4_fipr_fv(M, N).",
    "- While FPSCR.SZ is 1 (after an odd number of fschg), every fmov moves a register pair. An even register number N",
    "  names frN and frN+1; an odd one names xf(N-1) and xfN. The lower-numbered register goes to or from the lower",
    "  address, and pairs copy in order: `fmov fr0,@r4` writes fr0 at r4 and fr1 at r4+4; `fmov fr0,fr13` sets",
    "  xf12 = fr0 and xf13 = fr1; `fmov fr13,fr2` sets fr2 = xf12 and fr3 = xf13; `fmov.s @r4+,fr1` loads xf0, xf1.",
    "- tc_call leaves every FPU register and mode bit as it was, except fr0 = the call's scripted result (the test",
    "  harness mocks the callee, whatever the real one does).",
]


# The tc_rt.h helper for each SH-4 special instruction TcPrepare lists (meta sh4_special).
SPECIAL_HELPER = {"fsqrt": "sh4_fsqrt_rm", "fsrra": "sh4_fsrra", "fsca": "sh4_fsca", "fipr": "sh4_fipr",
                  "ftrv": "sh4_ftrv"}


def call_notes(meta):
    regs = {r for c in meta["callees"] for r in c["regs"]}
    lines = []
    if regs & {"r0", "r1", "r2", "r3"}:
        lines += ["", "r0-r3 in a callee's compared registers: that callee takes inputs outside r4-r7 (an SHC helper such",
                  "as a division routine). Set them in args.rlo[0..3] (r0..r3) before tc_call, to the values the original",
                  "has in those registers at the call."]
    if regs & {f"r{n}" for n in range(8, 15)}:
        lines += ["", "r8-r14 in a callee's compared registers: the callee reads them as runtime state. Before that",
                  "tc_call, give them the values the original has there with sh4_set_r(N, value)."]
    return lines


def context_md(meta, summary):
    files = [p.name for p in ghidra.packet_files(paths.PACKETS / meta["addr"])]
    lines = [f"# fn_{meta['addr']}", "", f"Signature: `{meta['signature']}`", "",
             "Source files, in reading order: " + ", ".join(f"`{f}`" for f in files), "", "## Parameters", ""]
    if not meta["params"]:
        lines.append("None.")
    for p in meta["params"]:
        where = p["storage"] if p["storage"] != "stack" else f"stack, @(0x{p['stack_offset']:x},r15) at entry"
        lines.append(f"- {p['name']} ({p['kind']}): {where}")
    lines += ["", f"r15 is 0x{entry_sp():08x} at entry: the function's stack frame (saved registers, locals) is guest memory",
              "just below it, and its stack parameters are at and above it."]
    lines += ["", f"Return: {meta['ret']} in {ret_reg(meta)}"]
    lines += abi_notes(meta)
    state = meta.get("fr_state") or []
    if state:
        lines += ["", "## Runtime state: " + ", ".join(state), "",
                  f"The original keeps values in {', '.join(state)} across calls (the vector library's fv12 vector).",
                  "They are not parameters: each test sets them at entry, and they are compared at exit. Read their",
                  "entry values with sh4_fr(N) and write them with sh4_set_fr(N, value) wherever the original writes",
                  "them (N = 12..15). A register the original never writes keeps its entry value: leave it alone."]
    lines += ["", "## Calls the original makes", ""]
    if not meta["callees"]:
        lines.append("Direct calls: none. Calls through pointers or tail jumps may still exist; check asm.txt.")
    for c in meta["callees"]:
        regs = ", ".join(c["regs"]) or "none"
        lines.append(f"- 0x{c['addr']}: Ghidra's guess `{c['signature']}`. Argument registers compared: {regs}")
    if any(r.startswith("s") for c in meta["callees"] for r in c["regs"]):
        lines += ["", "sN is a stack argument: the word at @(4N,r15) when the call is made. Set it in args.s[N]."]
    if any(r in ("fr12", "fr13", "fr14", "fr15") for c in meta["callees"] for r in c["regs"]):
        lines += ["", "fr12-fr15 are not in TcArgs: the callee reads them as runtime state. Before that tc_call, give",
                  "them the values the original has there with sh4_set_fr(N, value). After a call, read them again with",
                  "sh4_fr(N): a callee may change them (the test harness's mocked callees leave them as they are)."]
    lines += call_notes(meta)
    lines += ["", "Every call gets a scripted return value in r0/fr0 from the test harness. Use what tc_call returns.",
              "", "## Globals referenced through literal-pool constants", ""]
    lines += [f"- 0x{g}" for g in meta["globals"]] or ["None."]
    if meta.get("fpu_state"):
        lines += FPU_STATE
    if meta["sh4_special"]:
        helper = SPECIAL_HELPER | ({"ftrv": "sh4_ftrv_fv", "fipr": "sh4_fipr_fv"} if meta.get("fpu_state") else {})
        lines += ["", "## SH-4 special instructions used", "",
                  ", ".join(f"{m} ({helper[m]})" if m in helper else m for m in meta["sh4_special"])
                  + ". Use the matching sh4_* helpers in tc_rt.h, never your own version: they compute exactly what the"
                  " SH-4 does here (rounding mode, denormals as zero)."]
        if "frchg" in meta["sh4_special"]:
            lines += ["frchg swaps fr0-fr15 with xf0-xf15. After frchg, writes to fr registers land in xf (use sh4_set_xf)."
                      if not meta.get("fpu_state") else "frchg is sh4_frchg(): see the section above."]
    lines += ["", "## Test vectors", "",
              f"Dev vectors: {summary.get('dev_valid')}, hidden vectors: {summary.get('hidden_valid')}.",
              f"Basic blocks reached by the dev vectors: {summary.get('blocks_hit_dev')} of {summary.get('blocks_total')}.",
              "Inputs are random: pointers into a scratch area at 0x8cc00000, small integers, floats, and",
              "randomised bytes at the referenced globals. Unwritten memory keeps the game image contents (or zero)."]
    return "\n".join(lines) + "\n"


def build_sandbox(cfg, addr, meta, summary, prev_work, previous_text):
    box = paths.SANDBOX / addr
    if box.exists():
        for p in box.iterdir():
            os.chmod(p, stat.S_IWRITE)
        shutil.rmtree(box)
    box.mkdir(parents=True)
    packet = paths.PACKETS / addr
    sources = [p.name for p in ghidra.packet_files(packet)]
    image, ranges = cfg["binary_path"].read_bytes(), asm_pool.code_ranges(paths.CODE_RANGES)
    for name in sources:
        shutil.copyfile(packet / name, box / name)
        if name.startswith("asm"):  # batch 29: the words a pool `mova` points at
            text = (packet / name).read_text(encoding="utf-8")
            (box / name).write_text(asm_pool.annotate(text, image, int(cfg["load_address"], 16), ranges),
                                    encoding="utf-8")
    shutil.copyfile(paths.RUNTIME / "tc_rt.h", box / "tc_rt.h")
    (box / "context.md").write_text(context_md(meta, summary), encoding="utf-8")
    task = TASK.format(addr=addr, box=box, signature=meta["signature"], ret_reg=ret_reg(meta), entry_sp=entry_sp(),
                       previous=previous_text)
    (box / "TASK.md").write_text(task, encoding="utf-8")
    (box / "check.py").write_text(CHECK_LAUNCHER.format(tool=paths.TOOL / "decomp.py", project=paths.ROOT, addr=addr,
                                                            work=box / "work.c"),
                                  encoding="utf-8")
    if prev_work and os.path.exists(prev_work):
        shutil.copyfile(prev_work, box / "work.c")
    else:
        stub = {"void": "", "f32": "    return 0.0f;\n"}.get(meta["ret"], "    return 0;\n")
        (box / "work.c").write_text(f'#include "tc_rt.h"\n\n{meta["signature"]}\n{{\n{stub}}}\n', encoding="utf-8")
    for name in SANDBOX_FILES + tuple(sources):
        os.chmod(box / name, stat.S_IREAD)
    return box, task


def sandbox_hashes(box):
    out = {}
    for p in sorted(box.iterdir()):
        if p.name != "work.c" and p.is_file():
            out[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def describe_event(ev):
    """One short human-readable line per interesting stream-json event, or None."""
    t = ev.get("type")
    if t == "assistant":
        out = []
        for block in ev.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                inp = block.get("input", {})
                arg = inp.get("command") or inp.get("file_path") or ""
                out.append(f"{block.get('name')} {os.path.basename(str(arg)) if 'file_path' in inp else arg}".strip())
            elif block.get("type") == "text" and block.get("text", "").strip():
                out.append("says: " + block["text"].strip().splitlines()[0][:160])
        return " | ".join(out) or None
    if t == "user":
        for block in ev.get("message", {}).get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                content = block.get("content")
                text = content if isinstance(content, str) else " ".join(
                    c.get("text", "") for c in (content or []) if isinstance(c, dict))
                first = (text or "").strip().splitlines()[0][:160] if (text or "").strip() else ""
                if block.get("is_error"):
                    return "  -> denied/error: " + first
                if first.startswith(("DEV PASS", "DEV FAIL")):
                    return "  -> " + first
        return None
    if t == "result":
        return f"session ended: {ev.get('subtype')} cost ${ev.get('total_cost_usd', 0):.4f} turns {ev.get('num_turns')}"
    return None


RATE_RE = re.compile(r"rate.?limit|\b429\b|\b529\b|overloaded|usage limit", re.I)
AUTH_RE = re.compile(r"oauth|authenticat|\b401\b|\b403\b|/login|not logged in|invalid api key", re.I)


def api_error(final, output):
    """'rate_limit' or 'auth' when the session failed for API reasons rather than the agent's work, else None."""
    status = final.get("api_error_status")
    text = (final.get("result") or "") if final else (output or "")[-4000:]
    failed = final.get("is_error") or not final
    if status in (429, 529) or (failed and RATE_RE.search(text)):
        return "rate_limit"
    if status in (401, 403) or (failed and AUTH_RE.search(text)):
        return "auth"
    return None


def run_attempt(cfg, box, task, transcript_path, log, stop_flag, on_usage=None):
    """Run one locked-down session in the sandbox. Returns a summary dict.

    on_usage(rate_limit_info) receives the account's 5-hour/weekly utilisation as the CLI reports it."""
    prompt = "Follow TASK.md (reproduced below) exactly.\n\n" + task
    final = {}
    cap = OutputCap(cfg.get("output_cap"), stop_flag)  # cfg output_cap: stop after that many output tokens
    with open(transcript_path, "w", encoding="utf-8") as tf:
        def on_line(line):
            tf.write(line)
            tf.flush()
            try:
                ev = json.loads(line)
            except ValueError:
                return
            cap.feed(ev)
            if ev.get("type") == "result":
                final.update(ev)
            elif ev.get("type") == "system" and ev.get("subtype") == "init":
                final["_model"] = ev.get("model")
            elif ev.get("type") == "rate_limit_event" and on_usage:
                on_usage(ev.get("rate_limit_info"))
            msg = describe_event(ev)
            if msg:
                log(msg)

        rc, _out, reason = paths.run_bounded(claude_command(cfg), cfg["attempt_timeout_s"], cwd=box, env=agent_env(),
                                             stdin_text=prompt, on_line=on_line, stop_flag=cap)
    if cap.hit and reason == "stopped":
        reason = f"output_cap {cfg.get('output_cap')}"
    usage = final.get("usage") or {}
    return {
        "api_error": api_error(final, _out),
        "model_actual": final.get("_model"), "effort": cfg.get("effort") or "CLI default",
        "thinking_tokens": sum((m or {}).get("thinkingTokens") or 0 for m in (final.get("modelUsage") or {}).values()),
        "exit_code": rc, "killed": reason, "cost_usd": final.get("total_cost_usd"),
        "turns": final.get("num_turns"), "is_error": final.get("is_error"),
        "final_message": (final.get("result") or "")[-400:],
        "tokens_in": (usage.get("input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0)
        + (usage.get("cache_creation_input_tokens") or 0),
        "tokens_out": usage.get("output_tokens") or sum(cap.by_msg.values()) or None,
        "denials": len(final.get("permission_denials") or []),
    }
