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
    for m in re.finditer(rb"\x10\x2a\x11[\x00\x01\x40\x41]", data):
        c = container_at(data, m.start(), m.start() + base, 0x800)
        if c:
            out.append(c)
    return out


def container_at(data, o, addr, limit=0x10000):
    size, x = struct.unpack(">II", data[o + 4:o + 12])
    if not (0x30 <= size <= limit and 0 < x <= limit) or o + size + x > len(data):
        return None
    info = struct.unpack(">II", (data[o + struct.unpack(">I", data[o + 24:o + 28])[0]:][:8] + bytes(8))[:8])
    pre, prog = info if info[0] + info[1] == x else (0, x)
    blk = data[o + size:o + size + x]
    return {"addr": addr, "kind": "vs" if data[o + 3] & 1 else "ps", "size": size, "container": data[o:o + size],
            "prefix": blk[:pre], "ucode": blk[pre:pre + prog - prog % 12], "ucode_addr": addr + size + pre}


MADE = 0xF0000000


def made_key(blob):
    """The name runtime/xb_d3d.c (made_shader_key) gives a container the game makes at run time: FNV-1a over the container's
    bytes, and for a pixel shader its microcode too. A vertex shader's fetches are patched in its microcode when the game
    binds a declaration, so its microcode is left out; pack_blobs drops a key two different shaders share."""
    size, x = struct.unpack(">II", blob[4:12])
    h = 2166136261
    for b in blob[:size] if blob[3] & 1 else blob[:size + x]:
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return MADE | (h & ~MADE & 0xFFFFFFFF)


def pack_blobs(root):
    """Every shader container in the game's packs (the .gz files under root, unpacked in memory) with its microcode, by
    made_key: ({key: bytes}, the keys two different shaders share, left out)."""
    import gzip
    import os
    out, clash = {}, set()
    for dp, _, files in os.walk(root):
        for f in sorted(files):
            if f.endswith(".gz"):
                data = gzip.open(os.path.join(dp, f)).read()
                for m in re.finditer(rb"\x10\x2a\x11[\x00\x01\x40\x41]", data):
                    c = container_at(data, m.start(), 0)
                    if c:
                        blob = data[m.start():m.start() + c["size"] + struct.unpack(">I", data[m.start() + 8:m.start() + 12])[0]]
                        k = made_key(blob)
                        if out.get(k, blob) != blob:
                            clash.add(k)
                        out[k] = blob
    for k in clash:
        del out[k]
    return out, clash


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
    elif op == 11:   # COND_JMP, as Xenia's ucode.h ControlFlowCondJmpInstruction (see data/NOTICE-xenia.txt): the target is a control-flow index
        d.update(target=bits(v, 0, 13), uncond=bits(v, 13, 1), pred=bits(v, 14, 1), back=bits(v, 33, 1), bool_addr=bits(v, 34, 8), cond=bits(v, 42, 1))
    elif op == 12:
        d.update(alloc=bits(v, 41, 2) if False else bits(v, 40, 2), size=bits(v, 0, 4))
    return d


def walk_program(ucode, slots=None):
    """The program as a list of steps in execution order: ('cf', name) / ('alu'|'fetch', slot words, conditions). slots, a
    list, gets each step's slot number (None for a control-flow step)."""
    w, cfs = split_cf(ucode)
    steps = []
    nslots = len(w) // 3
    limit = nslots   # the control flow ends where the first instruction an exec names starts (two control-flow words to a slot)
    ahead = -1       # the furthest forward jump's target: an exec end before it ends the program only when the jump is not taken
    for idx, v in enumerate(cfs):
        if idx // 2 >= limit:
            break
        d = decode_cf(v)
        d["idx"] = idx
        if d["op"] == 11:
            ahead = max(ahead, d["target"])
        if d["op"] == 0 and not v:
            continue
        if d["op"] in EXEC_OPS:
            if d["count"]:
                limit = min(limit, d["addr"])
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
                if slots is not None:
                    slots.append(slot)
            if d["op"] not in (1, 2):
                steps.append(("cf", d["name"], d))
                if slots is not None:
                    slots.append(None)
        else:
            steps.append(("cf", d["name"], d))
            if slots is not None:
                slots.append(None)
        if d.get("end") and d["op"] == 2 and idx >= ahead:   # a conditional end ends the program only when its condition holds: what follows stays
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
         "predicated": bits(w1, 31, 1), "pred_cond": bits(w2, 31, 1)}
    if f["op"] == 0:
        f.update(format=bits(w1, 16, 6), stride=bits(w2, 0, 8), offset=bits(w2, 8, 23), src_swz=0)
    else:
        f.update(src_swz=bits(w0, 26, 6), tex_dim_ops=1, dim=bits(w2, 14, 2), denorm=bits(w0, 25, 1),
                 off=[(bits(w2, lo, 5) ^ 16) - 16 for lo in (16, 21, 26)])   # 0 1D, 1 2D, 2 3D or stacked, 3 cube; offsets signed 5 bits, halves
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


# The predicate (Xenia's ucode.h and dxbc_shader_translator_alu.cc, BSD-3-Clause, facts only; see data/NOTICE-xenia.txt). One bool, p0,
# false at the start. The scalar PRED_SET ops test a (the single operand) and set p0 and the scalar result; the vector *_PUSH ops set
# p0 from the operands' w and the result, all lanes, from their x, and run even with no lane written. Vector then scalar: when both
# set p0 the scalar's stands. A predicated instruction or fetch runs only while p0 equals its condition bit (ALU w1 bit 27, fetch w2
# bit 31; predicated: ALU w1 bit 28, fetch w1 bit 31); a COND_PRED_EXEC block only while p0 equals the exec's condition (bit 42),
# tested once at its start, and COND_PRED_EXEC_END ends the program only when it ran.
PRED_SCA = {
    27: "bool pn = {a} == 0.0; float s = pn ? 0.0 : 1.0;",
    28: "bool pn = {a} != 0.0; float s = pn ? 0.0 : 1.0;",
    29: "bool pn = {a} > 0.0; float s = pn ? 0.0 : 1.0;",
    30: "bool pn = {a} >= 0.0; float s = pn ? 0.0 : 1.0;",
    31: "bool pn = {a} == 1.0; float s = pn ? 0.0 : (({a} == 0.0) ? 1.0 : {a});",
    32: "float s = {a} - 1.0; bool pn = s <= 0.0; s = pn ? 0.0 : s;",
    33: "bool pn = false; float s = 3.402823466e+38;",
    34: "bool pn = {a} == 0.0; float s = pn ? 0.0 : {a};",
}
PRED_VEC = {20: "==", 21: "!=", 22: ">", 23: ">="}

# Cube maps, as Xenia's dxbc_shader_translator_alu.cc (kCube) and dxbc_shader_translator_fetch.cc (kCube; BSD-3-Clause, see
# data/NOTICE-xenia.txt). CUBEv takes its first source's swizzled z, w and x as the direction's x, y and z and gives T, S, twice the
# major axis and the face (0 +X, 1 -X, 2 +Y, 3 -Y, 4 +Z, 5 -Z). A cube fetch takes S and T plus 1 (1..2) and the face, and turns them
# back into a direction for the host's TextureCube, whose faces the runtime fills in the same order.
# The face's sign is a factor, not a ternary between v and -v: fxc turns that into a movc with a negated operand, which D3D11 bytecode does
# not allow (X8000 on six of Too Human's eight cube shaders, 2026-11-07)
CUBE_ALU_FN = """float4 xb_cube(float4 s)
{
    float x = s.z, y = s.w, z = s.x, n;
    if (abs(z) >= abs(x) && abs(z) >= abs(y)) { n = z < 0.0 ? -1.0 : 1.0; return float4(-y, x * n, 2.0 * z, 4.5 - 0.5 * n); }
    if (abs(y) >= abs(x)) { n = y < 0.0 ? -1.0 : 1.0; return float4(z * n, x, 2.0 * y, 2.5 - 0.5 * n); }
    n = x < 0.0 ? -1.0 : 1.0;
    return float4(-y, -z * n, 2.0 * x, 0.5 - 0.5 * n);
}"""
CUBE_DIR_FN = """float3 xb_cube_dir(float3 c)
{
    float2 st = c.xy * 2.0 - 3.0;
    uint f = min((uint)max(c.z, 0.0), 5u);
    float n = (f & 1u) != 0 ? -1.0 : 1.0;
    if (f < 2u) return float3(n, -st.y, -st.x * n);
    if (f < 4u) return float3(st.x, n, st.y * n);
    return float3(st.x * n, -st.y, n);
}"""


def pred_vec(vop, a, b):
    """The *_PUSH vector op on the swizzled operands a and b: (statement setting bool pv, expression of the float4 result)."""
    o = PRED_VEC[vop]
    return ("bool pv = %s.w == 0.0 && %s.w %s 0.0;" % (a, b, o),
            "(float4)((%s.x == 0.0 && %s.x %s 0.0) ? 0.0 : %s.x + 1.0)" % (a, b, o, a))


# ---- conversion to HLSL -------------------------------------------------------------------------------------------------

