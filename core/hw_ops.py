"""Listed hardware ops (project.json "hw_ops"): a function whose only parts C cannot express are single hardware
operations, each listed with its evidence, is judged on everything else (TcHw.java models each listed op).

  "hw_ops": {"8c04f4c0": {"ops": ["read a05f689c", "ldc gbr"],
                          "evidence": "Disp_ApplyMode: reads SB_SBREV once (8c04f514), sets GBR (8c04f536)"}}

One entry in "ops" per instruction:
  read R      a load from the hardware register R (8 hex digits, as the pool constant holds it); the value is the
              target's (target.json "hw_reads": {R: [value, evidence]}); the C reads it with tc_hw_read32(R)
              A register narrower than 4 bytes gives its width third: {R: [value, evidence, 1 or 2]} (Saturn's FRT
              FTCSR, a byte at 0xfffffe11); the C reads it with tc_hw_read8(R) / tc_hw_read16(R). Only widths the
              target's runtime serves (target.json "hw_read_sizes", default [4]) are accepted
  ldc gbr     a write of GBR (sh4_ldc_gbr); stc gbr: a read of it (sh4_stc_gbr). GBR is state, compared at exit
  ldc sr.bl   an SR write that sets SR.BL (exceptions blocked); the C writes SR with sh4_ldc_sr as any SR write
  write R     a store to the hardware register R: logged and compared as a memory write (as the P4 cache-array
              stores, tc_p4.c); the C stores with tc_hw_write32(R, v) or tc_hw_write16(R, v)
  ocbi, ocbp, ocbwb   a cache block op: a no-op on both sides (memory is modelled coherent; sh4_ocbi etc. in C)

Opt-in per address, as "spin_waits": the catalog (TcCatalog) counts the function's hardware ops, and the queue stops
excluding it (core/orch.tier) only when the listing covers every one it counts: hw_refs no more than the listed reads
and writes, sys_ops exactly the listed GBR moves and cache ops (SR moves are TcCatalog's sr_ops; a catalog older than sr_ops counts them as
sys_ops and must be rebuilt). TcPrepare checks the body instruction by instruction (TcHw.check): an op the listing
does not name leaves the function unsupported."""
import re

KINDS = ("read", "write", "ldc gbr", "stc gbr", "ldc sr.bl", "ocbi", "ocbp", "ocbwb")
_READ = re.compile(r"read ([0-9a-f]{8})")
_WRITE = re.compile(r"write ([0-9a-f]{8})")
CACHE = ("ocbi", "ocbp", "ocbwb")


def width(cfg, reg):
    """The byte width of the listed hardware register reg (target.json "hw_reads" entry [value, evidence] or
    [value, evidence, width]): 4 unless given. A width the target's runtime does not serve (target.json
    "hw_read_sizes", default [4]), or a value wider than the register, is refused (ValueError)."""
    e = {k.lower(): v for k, v in (cfg.get("hw_reads") or {}).items()}[reg.lower()]
    w = e[2] if len(e) > 2 else 4
    if w not in (cfg.get("hw_read_sizes") or [4]):
        raise ValueError(f'"hw_reads" {reg}: a {w}-byte register, which this target does not serve '
                         f'(target.json "hw_read_sizes": {cfg.get("hw_read_sizes") or [4]})')
    if int(e[0], 16) >> (8 * w):
        raise ValueError(f'"hw_reads" {reg}: value {e[0]} does not fit its {w} byte(s)')
    return w


def listed(cfg):
    """{addr: ops} of project.json "hw_ops", each entry checked: known ops, a known register for each read, evidence.
    A malformed entry is refused (ValueError), never silently ignored."""
    hw = cfg.get("hw_ops") or {}
    if not isinstance(hw, dict):
        raise ValueError('project.json "hw_ops" must map each address to {"ops": [...], "evidence": "..."}')
    regs = {k.lower() for k in (cfg.get("hw_reads") or {})}
    out = {}
    for addr, e in hw.items():
        if not isinstance(e, dict) or not isinstance(e.get("evidence"), str) or not e["evidence"].strip():
            raise ValueError(f'project.json "hw_ops" {addr}: no evidence (say where each op is and what it does)')
        ops = e.get("ops")
        if not isinstance(ops, list) or not ops:
            raise ValueError(f'project.json "hw_ops" {addr}: "ops" must list each hardware op')
        for op in ops:
            m = _READ.fullmatch(str(op).lower())
            if not m and not _WRITE.fullmatch(str(op).lower()) and op not in KINDS[2:]:
                raise ValueError(f'project.json "hw_ops" {addr}: unknown op {op!r} (one of: read R, write R, '
                                 + ", ".join(KINDS[2:]) + ")")
            if m and m.group(1) not in regs:
                raise ValueError(f'project.json "hw_ops" {addr}: register {m.group(1)} has no value in target.json '
                                 '"hw_reads" (add it there with its evidence)')
            if m:
                width(cfg, m.group(1))
        out[addr.lower()] = [str(op).lower() for op in ops]
    return out


def covered(ops, f):
    """Whether the listed ops cover every hardware op the catalog counts for f (a catalog entry)."""
    if ops is None:
        return False
    regs = sum(bool(_READ.fullmatch(op) or _WRITE.fullmatch(op)) for op in ops)
    sys_ops = sum(op in ("ldc gbr", "stc gbr", *CACHE) for op in ops)
    return f.get("hw_refs", 0) <= regs and f.get("sys_ops", 0) == sys_ops


def arg(cfg, addr):
    """TcPrepare's script arg 11: "-" for a function hw_ops does not list, else "hw:" and "_"-separated items
    (TcHw.parse): "r" + register + "." + value per register read (2 hex digits per byte of the register's width: 8
    for the default 4), "w" + register per register stored to, "gbr", "bl", "cache". No ",;= ": analyzeHeadless.bat
    splits arguments at them."""
    ops = listed(cfg).get(addr.lower())
    if ops is None:
        return "-"
    vals = {k.lower(): v for k, v in (cfg.get("hw_reads") or {}).items()}
    items = [f"r{r}.{int(vals[r][0], 16):0{2 * width(cfg, r)}x}"
             for r in sorted({m.group(1) for m in map(_READ.fullmatch, ops) if m})]
    items += [f"w{r}" for r in sorted({m.group(1) for m in map(_WRITE.fullmatch, ops) if m})]
    items += ["gbr"] * any(op.endswith("gbr") for op in ops) + ["bl"] * ("ldc sr.bl" in ops)
    items += ["cache"] * any(op in CACHE for op in ops)
    return "hw:" + "_".join(items)
