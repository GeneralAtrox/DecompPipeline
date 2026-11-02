"""Decoding of the Xenos shader containers and microcode for shader_convert.py, which writes the HLSL. Layouts and opcode numbers: see there.

Nearest existing: targets/xbox360_ppc/port/tools/shader_convert.py - that file is the converter; this half reads the bits, split off for the 400-line cap.
"""
import re
import struct

VEC = {0: "ADDv", 1: "MULv", 2: "MAXv", 3: "MINv", 4: "SETEv", 5: "SETGTv", 6: "SETGTEv", 7: "SETNEv", 8: "FRACv", 9: "TRUNCv",
       10: "FLOORv", 11: "MULADDv", 12: "CNDEv", 13: "CNDGTEv", 14: "CNDGTv", 15: "DOT4v", 16: "DOT3v", 17: "DOT2ADDv",
       18: "CUBEv", 19: "MAX4v", 20: "PRED_SETE_PUSHv", 21: "PRED_SETNE_PUSHv", 22: "PRED_SETGT_PUSHv",
       23: "PRED_SETGTE_PUSHv", 24: "KILLEv", 25: "KILLGTv", 26: "KILLGTEv", 27: "KILLNEv", 28: "DSTv", 29: "MOVAv"}
SCA = {0: "ADDs", 1: "ADD_PREVs", 2: "MULs", 3: "MUL_PREVs", 4: "MUL_PREV2s", 5: "MAXs", 6: "MINs", 7: "SETEs", 8: "SETGTs",
       9: "SETGTEs", 10: "SETNEs", 11: "FRACs", 12: "TRUNCs", 13: "FLOORs", 14: "EXP_IEEE", 15: "LOG_CLAMP", 16: "LOG_IEEE",
       17: "RECIP_CLAMP", 18: "RECIP_FF", 19: "RECIP_IEEE", 20: "RECIPSQ_CLAMP", 21: "RECIPSQ_FF", 22: "RECIPSQ_IEEE",
       23: "MOVAs", 24: "MOVA_FLOORs", 25: "SUBs", 26: "SUB_PREVs", 27: "PRED_SETEs", 28: "PRED_SETNEs", 29: "PRED_SETGTs",
       30: "PRED_SETGTEs", 31: "PRED_SET_INVs", 32: "PRED_SET_POPs", 33: "PRED_SET_CLRs", 34: "PRED_SET_RESTOREs",
       35: "KILLEs", 36: "KILLGTs", 37: "KILLGTEs", 38: "KILLNEs", 39: "KILLONEs", 40: "SQRT_IEEE", 42: "MUL_CONST_0",
       43: "MUL_CONST_1", 44: "ADD_CONST_0", 45: "ADD_CONST_1", 46: "SUB_CONST_0", 47: "SUB_CONST_1", 48: "SIN", 49: "COS",
       50: "RETAIN_PREV"}
CF = {0: "NOP", 1: "EXEC", 2: "EXEC_END", 3: "COND_EXEC", 4: "COND_EXEC_END", 5: "COND_PRED_EXEC", 6: "COND_PRED_EXEC_END",
      7: "LOOP_START", 8: "LOOP_END", 9: "COND_CALL", 10: "RETURN", 11: "COND_JMP", 12: "ALLOC", 13: "COND_EXEC_PRED_CLEAN",
      14: "COND_EXEC_PRED_CLEAN_END", 15: "MARK_VS_FETCH_DONE"}
FETCH = {0: "VFETCH", 1: "TFETCH", 2: "TFETCH_GETGRAD", 3: "TFETCH_GETWEIGHTS", 4: "TFETCH_SETGRAD_H", 5: "TFETCH_SETGRAD_V",
         7: "TFETCH_SETOFFSET", 8: "SETGRAD"}
EXEC_OPS = (1, 2, 3, 4, 5, 6, 13, 14)
EXEC_END = (2, 4, 6, 14)
SWZ = "xyzw"
# opcodes with the operand pattern the generator needs: (kind, sources used)
VEC_SRCS = {0: 2, 1: 2, 2: 2, 3: 2, 4: 2, 5: 2, 6: 2, 7: 2, 8: 1, 9: 1, 10: 1, 11: 3, 12: 3, 13: 3, 14: 3, 15: 2, 16: 2, 17: 3}


class Unsupported(Exception):
    pass


def bits(v, lo, n):
    return (v >> lo) & ((1 << n) - 1)


def find_containers(data, base):
    """Every shader container in the image: [{addr, kind, size, container, prefix, ucode}]. The microcode block follows the
    container at once, `X` bytes (header word 2). The block info at header word 6 starts with two sizes: the bytes of
    constants the compiler embedded in front of the program, then the program itself (X is their sum)."""
    out = []
    for m in re.finditer(rb"\x10\x2a\x11[\x00\x01]", data):
        o = m.start()
        size, x = struct.unpack(">II", data[o + 4:o + 12])
        if not (0x30 <= size <= 0x800 and 0 < x <= 0x800):
            continue
        info = struct.unpack(">II", data[o + struct.unpack(">I", data[o + 24:o + 28])[0]:][:8])
        pre, prog = info if info[0] + info[1] == x else (0, x)
        blk = data[o + size:o + size + x]
        out.append({"addr": o + base, "kind": "ps" if data[o + 3] == 0 else "vs", "size": size, "container": data[o:o + size],
                    "prefix": blk[:pre], "ucode": blk[pre:pre + prog - prog % 12], "ucode_addr": o + base + size + pre})
    return out


def ctab(container):
    """The constant table the compiler left in the container: [(name, register_set, register_index, register_count)].
    It is a D3DX constant table that starts 4 bytes into the block at header word 4."""
    t = container[struct.unpack(">I", container[16:20])[0] + 4:]
    cnt, ci = struct.unpack(">II", t[12:20])
    rows = []
    for k in range(cnt):
        e = ci + 20 * k
        noff, rset, ridx, rcnt = struct.unpack(">IHHH", t[e:e + 10])
        rows.append((t[noff:t.index(b"\0", noff)].decode("latin1"), rset, ridx, rcnt))
    return rows


def split_cf(ucode):
    """The control-flow words and the slot number where instructions start."""
    w = struct.unpack(">%dI" % (len(ucode) // 4), ucode)
    cfs = []
    for g in range(0, len(w) - 2, 3):
        a = w[g] | ((w[g + 1] & 0xFFFF) << 32)
        b = (w[g + 1] >> 16) | (w[g + 2] << 16)
        cfs += [a, b]
    return w, cfs


def decode_cf(v):
    op = bits(v, 44, 4)
    d = {"op": op, "name": CF[op]}
    if op in EXEC_OPS:
        d.update(addr=bits(v, 0, 12), count=bits(v, 12, 3), seq=bits(v, 16, 12), end=op in EXEC_END,
                 cond=bits(v, 42, 1), bool_addr=bits(v, 34, 8), pred_cond=bits(v, 42, 1))
        if d["count"] == 0 and d["seq"] == 0 and op in (1, 2):
            pass
    elif op == 12:
        d.update(alloc=bits(v, 41, 2) if False else bits(v, 40, 2), size=bits(v, 0, 4))
    return d


def walk_program(ucode):
    """The program as a list of steps in execution order: ('cf', name) / ('alu'|'fetch', slot words, conditions)."""
    w, cfs = split_cf(ucode)
    steps = []
    nslots = len(w) // 3
    for v in cfs:
        d = decode_cf(v)
        if d["op"] == 0 and not v:
            continue
        if d["op"] in EXEC_OPS:
            if d["count"] == 0 and d["seq"] == 0:
                # an empty exec is the padding word after the last
                if not d["addr"]:
                    continue
            n = d["count"] if d["count"] else (8 if False else 0)
            for i in range(d["count"]):
                slot = d["addr"] + i
                if slot >= nslots:
                    raise Unsupported("exec past the end of the program")
                kind = "fetch" if (d["seq"] >> (2 * i)) & 1 else "alu"
                steps.append((kind, w[slot * 3:slot * 3 + 3], d))
            if d["op"] not in (1, 2):
                steps.append(("cf", d["name"], d))
        else:
            steps.append(("cf", d["name"], d))
        if d.get("end"):
            break
    return steps, nslots


def alu_fields(x):
    w0, w1, w2 = x
    return {
        "vdest": bits(w0, 0, 6), "vdest_rel": bits(w0, 6, 1), "abs_const": bits(w0, 7, 1), "sdest": bits(w0, 8, 6),
        "sdest_rel": bits(w0, 14, 1), "export": bits(w0, 15, 1), "vmask": bits(w0, 16, 4), "smask": bits(w0, 20, 4),
        "vclamp": bits(w0, 24, 1), "sclamp": bits(w0, 25, 1), "sop": bits(w0, 26, 6),
        "swz": [bits(w1, 16, 8), bits(w1, 8, 8), bits(w1, 0, 8)],
        "neg": [bits(w1, 26, 1), bits(w1, 25, 1), bits(w1, 24, 1)],
        "pred_cond": bits(w1, 27, 1), "predicated": bits(w1, 28, 1), "addr_abs": bits(w1, 29, 1),
        "c1_rel": bits(w1, 30, 1), "c0_rel": bits(w1, 31, 1),
        "reg": [bits(w2, 16, 8), bits(w2, 8, 8), bits(w2, 0, 8)],
        "vop": bits(w2, 24, 5),
        "sel": [bits(w2, 31, 1), bits(w2, 30, 1), bits(w2, 29, 1)],   # 1: temporary register, 0: constant
    }


def fetch_fields(x):
    w0, w1, w2 = x
    f = {"op": bits(w0, 0, 5), "src": bits(w0, 5, 6), "src_am": bits(w0, 11, 1), "dst": bits(w0, 12, 6),
         "dst_am": bits(w0, 18, 1), "const": bits(w0, 20, 5), "const_sel": bits(w0, 25, 2), "dst_swz": bits(w1, 0, 12),
         "pred": bits(w2, 0, 0)}
    if f["op"] == 0:
        f.update(format=bits(w1, 16, 6), stride=bits(w2, 0, 8), offset=bits(w2, 8, 24), src_swz=0)
    else:
        f.update(src_swz=bits(w0, 26, 6), tex_dim_ops=1)
    return f


def swz_str(sw, mask_only=None):
    return "".join(SWZ[(i + bits(sw, 2 * i, 2)) & 3] for i in range(4))


def disasm(steps):
    lines = []
    for kind, x, d in steps:
        if kind == "cf":
            lines.append("  cf %s" % x)
        elif kind == "fetch":
            f = fetch_fields(x)
            lines.append("  %s r%d <- r%d const %d sel %d dstswz %03x%s" % (
                FETCH.get(f["op"], "FETCH?%d" % f["op"]), f["dst"], f["src"], f["const"], f["const_sel"], f["dst_swz"],
                (" fmt %d stride %d off %d" % (f["format"], f["stride"], f["offset"])) if f["op"] == 0 else ""))
        else:
            a = alu_fields(x)
            src = []
            for i in range(3):
                rg = a["reg"][i]
                src.append("%s%d.%s%s" % ("r" if a["sel"][i] else "c", rg & 0x3F if a["sel"][i] else rg, swz_str(a["swz"][i]),
                                           "!" if a["neg"][i] else ""))
            lines.append("  %s %s%d.%s ; %s %s%d.%s  src %s%s" % (
                VEC.get(a["vop"], "VEC?%d" % a["vop"]), "o" if a["export"] else "r", a["vdest"],
                "".join(c for i, c in enumerate(SWZ) if a["vmask"] >> i & 1), SCA.get(a["sop"], "SCA?%d" % a["sop"]),
                "o" if a["export"] else "r", a["sdest"], "".join(c for i, c in enumerate(SWZ) if a["smask"] >> i & 1),
                " ".join(src), " clamp" if a["vclamp"] else ""))
    return lines


# ---- conversion to HLSL -------------------------------------------------------------------------------------------------

