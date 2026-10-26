// Test-input generation for the Xbox 360 PowerPC target: registers, scratch memory, scripted call results,
// per-function hints and pointer repair. Extended by TcOracle (locked), which runs the ORIGINAL code on these inputs.
//
// This is the only oracle file the watcher agent may change. It decides inputs only: whatever it generates, the
// expected results still come from running the original machine code. Rules (core/javalint.py): the only import is
// java.util.*; no I/O, reflection, threads, lambdas, Ghidra API or script state; only allow-listed library calls; new
// top-level members are named gen*, Gen* or GEN_*; never assign rnd; never try to tell dev and hidden vectors apart.
// Guest memory is big endian: every word a patch holds is written most significant byte first.
//
// Registers are 64 bits. An integer argument gets the extension its declared kind implies (u32 and pointers zero,
// s32 sign); u64/s64 arguments sometimes get a random upper word. Whether a 32-bit parameter's upper word matters is
// TcPrepare's width probe, not this file's.
import java.util.*;

public abstract class TcInputs extends TcBase {
    static final long GEN_HEAP_LO = 0x40001000L;   // above the scratch window, to the end of the heap region
    static final long GEN_HEAP_HI = 0x40100000L;
    static final long GEN_STACK_LO = 0x70000000L;
    static final long GEN_STACK_HI = 0x70100000L;
    static final long GEN_KERNEL_LO = 0x7fff0000L;
    static final long GEN_KERNEL_HI = 0x80000000L;
    static final long GEN_IMAGE_LO = 0x82000000L;
    static final long GEN_STACK_ARGS = 0x50L;      // r1 + 0x50 + 8N: the Nth stack doubleword argument
    static final int GEN_CHUNK = 0x400;
    static final int GEN_MAX_OFFSET = 0x4000;

    static class Vec {
        long[] r = new long[8];       // r3..r10, 64 bits each
        long[] f = new long[13];      // f1..f13, the 64-bit register contents (IEEE double bits)
        long[] rets = new long[16];   // what mocked call k leaves in r3 (64 bits)
        long[] frets = new long[16];  // and in f1 (double bits)
        List<Long> patchAddr = new ArrayList<>();
        List<byte[]> patchData = new ArrayList<>();
        long genHeapNext = GEN_HEAP_LO;
        Map<Long, Long> genPoked = new HashMap<>();
    }

    // ---------- random values ----------
    int niceFloat() {
        switch (rnd.nextInt(10)) {
            case 0: return Float.floatToIntBits(0f);
            case 1: return Float.floatToIntBits(1f);
            case 2: return Float.floatToIntBits(-1f);
            case 3: return Float.floatToIntBits(0.5f);
            case 4: return Float.floatToIntBits((float) (rnd.nextInt(201) - 100));
            case 5: return Float.floatToIntBits(rnd.nextFloat() * 2 - 1);
            case 6: return Float.floatToIntBits((rnd.nextFloat() * 2 - 1) * 1000f);
            case 7: return Float.floatToIntBits(rnd.nextFloat() * 255f);
            case 8: return Float.floatToIntBits((rnd.nextFloat() * 2 - 1) * 65536f);
            default: {
                int bits;
                do {
                    bits = rnd.nextInt();
                } while (((bits >> 23) & 0xff) == 0xff);
                return bits;
            }
        }
    }

    // A double holding a single-precision value (what lfs leaves in an FPR).
    long genSingleDouble() {
        return Double.doubleToLongBits((double) Float.intBitsToFloat(niceFloat()));
    }

    // A double: mostly a single-precision value, sometimes a full double (a callee's double result, fctiwz input).
    long genDouble() {
        int k = rnd.nextInt(10);
        if (k < 6) return genSingleDouble();
        if (k < 8) return Double.doubleToLongBits((double) (rnd.nextInt(2001) - 1000) + rnd.nextDouble());
        return Double.doubleToLongBits((rnd.nextDouble() * 2 - 1) * 70000.0);
    }

    long scrPtr() {
        return SCR + 4L * rnd.nextInt((int) (SCR_SIZE / 4));
    }

    // A 32-bit memory word.
    long word() {
        int k = rnd.nextInt(100);
        if (k < 30) return scrPtr();
        if (k < 50) return rnd.nextInt(16);
        if (k < 65) return niceFloat() & 0xffffffffL;
        if (k < 75) return 0;
        if (k < 80) return 0xffffffffL;
        if (k < 90) return rnd.nextInt(0x10000);
        return rnd.nextInt() & 0xffffffffL;
    }

    // A 32-bit argument value (the low word); makeVec extends it to the register's 64 bits.
    long intArg(boolean pointer) {
        int k = rnd.nextInt(100);
        if (pointer) return k < 88 ? scrPtr() : 0;
        if (k < 40) return scrPtr();
        if (k < 55) return 0;
        if (k < 75) return rnd.nextInt(17);
        if (k < 82) return (-1 - rnd.nextInt(8)) & 0xffffffffL;
        if (k < 90) return niceFloat() & 0xffffffffL;
        return rnd.nextInt() & 0xffffffffL;
    }

    // The 64-bit register value of a 32-bit value w for a parameter of `kind`.
    long genExtend(long w, String kind) {
        w = w & 0xffffffffL;
        if (kind.equals("s32")) return (long) (int) w;
        if (kind.equals("u64") || kind.equals("s64")) {
            int k = rnd.nextInt(100);
            if (k < 15) return (long) (int) w;
            if (k < 25) return rnd.nextLong();
        }
        return w;
    }

    void genPut(byte[] b, int at, long w) {
        for (int k = 0; k < 4; k++) b[at + k] = (byte) (w >> (24 - 8 * k));
    }

    void genPut64(byte[] b, int at, long w) {
        for (int k = 0; k < 8; k++) b[at + k] = (byte) (w >> (56 - 8 * k));
    }

    byte[] scratchBytes(int flavor) {
        byte[] b = new byte[(int) SCR_SIZE];
        if (flavor == 1) {
            String alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:;, -";
            for (int i = 0; i < b.length; i++) {
                int k = rnd.nextInt(40);
                b[i] = (byte) (k == 0 ? 0 : alphabet.charAt(rnd.nextInt(alphabet.length())));
            }
            return b;
        }
        if (flavor == 2) {
            for (int i = 0; i < b.length; i++) b[i] = (byte) rnd.nextInt(8);
            return b;
        }
        for (int i = 0; i < b.length; i += 4) {
            int k = rnd.nextInt(100);
            long w = flavor != 3 ? word() : k < 55 ? scrPtr() : k < 80 ? niceFloat() & 0xffffffffL : rnd.nextInt(16);
            genPut(b, i, w);
        }
        return b;
    }

    // ---------- one vector ----------
    // flavor 0 words, 1 text, 2 small bytes, 3 pointer-rich. globals: the writable globals the code names.
    Vec makeVec(int flavor, Proto proto, List<Long> globals) {
        Vec v = new Vec();
        v.patchAddr.add(SCR);
        v.patchData.add(scratchBytes(flavor));
        for (int i = 0; i < 8; i++) v.r[i] = intArg(false);
        for (int i = 0; i < 13; i++) v.f[i] = genSingleDouble();
        for (int i = 0; i < 16; i++) {
            int k = rnd.nextInt(10);
            v.rets[i] = k < 4 ? rnd.nextInt(4) : (k < 6 ? scrPtr() : (k < 8 ? 0 : (long) rnd.nextInt()));
            v.frets[i] = genDouble();
        }
        int stackWords = 0;
        for (int i = 0; i < proto.params.size(); i++) {
            Param q = proto.params.get(i);
            boolean ptr = proto.paramIsPointer.get(i);
            if (q.storage.matches("r([3-9]|10)")) {
                long iv = intArg(ptr);
                if (flavor == 3 && !ptr && rnd.nextInt(100) < 70) iv = 1 + rnd.nextInt(12);
                if (q.kind.equals("f32")) iv = niceFloat() & 0xffffffffL;
                v.r[Integer.parseInt(q.storage.substring(1)) - 3] = ptr ? iv : genExtend(iv, q.kind);
            } else if (q.storage.matches("f([1-9]|1[0-3])")) {
                v.f[Integer.parseInt(q.storage.substring(1)) - 1] = q.kind.equals("f64") ? genDouble() : genSingleDouble();
            } else if (q.storage.equals("stack")) {
                stackWords = Math.max(stackWords, (int) ((q.stackOffset - GEN_STACK_ARGS) / 8) + 1);
            }
        }
        if (stackWords > 0) {
            byte[] st = new byte[stackWords * 8];
            for (int i = 0; i < stackWords; i++) genPut64(st, i * 8, genExtend(intArg(false), "u32"));
            v.patchAddr.add(SP + GEN_STACK_ARGS);
            v.patchData.add(st);
        }
        for (long g : globals) {
            if (rnd.nextInt(10) >= 7) continue;
            byte[] d = new byte[32];
            for (int i = 0; i < 32; i += 4) genPut(d, i, word());
            v.patchAddr.add(g & ~3L);
            v.patchData.add(d);
        }
        return v;
    }

    // ---------- hints: oracle/hints/<addr>.txt, applied to every vector of that function ----------
    //   genrange LOC LO HI    LOC (a register rN, or a word, see below) is uniform in [LO, HI] (decimal or 0x hex)
    //   genptr LOC            the word at LOC holds a pointer to a fresh chunk of pointer-rich memory
    //   genstream LOC N V..   the word at LOC holds a pointer to a fresh stream of N words, each picked from V..
    //                         ("any" picks a random word, "float" a random float's bits)
    //   genret N VAL          mocked call N (0-based) leaves VAL in r3 ("sentinel": the oracle's return address)
    //   genfret N HEX64       and HEX64 (double bits) in f1
    // LOC is rN or 0xADDR (a global), optionally followed by +0xOFF steps; each step dereferences the pointer before it:
    // "r4+0x1c" is the word at r4+0x1c, "0x82346720+0x10" the word at *(0x82346720)+0x10. Pointers are 32 bits.
    void applyHints(Vec v, List<String> hints) {
        for (String line : hints) {
            String s = line.trim();
            if (s.isEmpty() || s.startsWith("#")) continue;
            String[] t = s.split("\\s+");
            if (t[0].equals("genrange") && t.length == 4) {
                long lo = genNum(t[2]);
                long span = Math.max(1, genNum(t[3]) - lo + 1);
                genPlace(v, t[1], (lo + (long) (rnd.nextDouble() * span)) & 0xffffffffL);
            } else if (t[0].equals("genptr") && t.length == 2) {
                genPlace(v, t[1], genChunk(v, 0));
            } else if (t[0].equals("genstream") && t.length >= 4) {
                genPlace(v, t[1], genStream(v, (int) genNum(t[2]), Arrays.copyOfRange(t, 3, t.length)));
            } else if (t[0].equals("genret") && t.length == 3) {
                int n = (int) genNum(t[1]);
                if (n < 0 || n >= v.rets.length) throw new IllegalArgumentException("bad genret index: " + s);
                v.rets[n] = t[2].equals("sentinel") ? SENTINEL : genNum(t[2]);
            } else if (t[0].equals("genfret") && t.length == 3) {
                int n = (int) genNum(t[1]);
                if (n < 0 || n >= v.frets.length) throw new IllegalArgumentException("bad genfret index: " + s);
                v.frets[n] = Long.parseUnsignedLong(t[2].startsWith("0x") ? t[2].substring(2) : t[2], 16);
            } else {
                throw new IllegalArgumentException("bad hint line: " + s);
            }
        }
    }

    long genNum(String s) {
        return s.startsWith("0x") ? Long.parseLong(s.substring(2), 16) : Long.parseLong(s);
    }

    long genStream(Vec v, int n, String[] vals) {
        long base = genChunk(v, 4L * n);
        byte[] d = new byte[4 * n];
        for (int i = 0; i < n; i++) {
            String w = vals[rnd.nextInt(vals.length)];
            long x = w.equals("any") ? word() : w.equals("float") ? niceFloat() & 0xffffffffL : genNum(w);
            genPut(d, 4 * i, x);
        }
        v.patchAddr.add(base);
        v.patchData.add(d);
        return base;
    }

    // Store val at LOC (see the hint format above); a bare rN receives val itself.
    void genPlace(Vec v, String loc, long val) {
        String[] p = loc.split("\\+");
        long slot;
        if (p[0].matches("r([3-9]|10)")) {
            int i = Integer.parseInt(p[0].substring(1)) - 3;
            if (p.length == 1) {
                v.r[i] = val;
                return;
            }
            if (!genOwned(v.r[i])) v.r[i] = genChunk(v, 0);
            slot = v.r[i] + genNum(p[1]);
        } else {
            slot = genNum(p[0]);
            if (p.length > 1) slot = genDeref(v, slot) + genNum(p[1]);
        }
        for (int k = 2; k < p.length; k++) slot = genDeref(v, slot) + genNum(p[k]);
        genPoke(v, slot, val, 4);
    }

    boolean genOwned(long a) {
        return (a >= SCR && a < SCR + SCR_SIZE) || (a >= GEN_HEAP_LO && a < GEN_HEAP_HI);
    }

    long genDeref(Vec v, long slot) {
        Long have = v.genPoked.get(slot);
        if (have != null) return have;
        long p = genChunk(v, 0);
        genPoke(v, slot, p, 4);
        return p;
    }

    // In one of the four regions (the image's end is TcVectors' check: patches must lie in mapped memory).
    boolean genInRam(long a) {
        return (a >= SCR && a + 4 <= GEN_HEAP_HI) || (a >= GEN_STACK_LO && a + 4 <= GEN_STACK_HI)
            || (a >= GEN_KERNEL_LO && a + 4 <= GEN_KERNEL_HI) || (a >= GEN_IMAGE_LO && a + 4 <= RAM_HI);
    }

    // A word (size 4) or a doubleword (size 8, the pointer in its low word) holding val at addr.
    void genPoke(Vec v, long addr, long val, int size) {
        if (!genInRam(addr)) throw new IllegalArgumentException("cannot set a word outside RAM: " + Long.toHexString(addr));
        byte[] d = new byte[size];
        if (size == 8) genPut64(d, 0, val);
        else genPut(d, 0, val);
        v.patchAddr.add(addr);
        v.patchData.add(d);
        v.genPoked.put(addr, val);
    }

    // A fresh pointer-rich chunk big enough for an access `offset` bytes from the returned pointer.
    long genChunk(Vec v, long offset) {
        long lead = 0x100;
        long size = lead + Math.max(offset, 0) + 0x100;
        size = Math.max(GEN_CHUNK, (size + 0xff) & ~0xffL);
        if (v.genHeapNext + size > GEN_HEAP_HI) return scrPtr();
        long base = v.genHeapNext;
        v.genHeapNext += size;
        byte[] d = new byte[(int) size];
        for (int i = 0; i < d.length; i += 4) {
            int k = rnd.nextInt(100);
            long w = k < 10 ? scrPtr() : k < 60 ? rnd.nextInt(16) : k < 75 ? niceFloat() & 0xffffffffL : 0;
            genPut(d, i, w);
        }
        v.patchAddr.add(base);
        v.patchData.add(d);
        return base + lead;
    }

    // ---------- pointer repair ----------
    // Called after a run faulted at faultAddr (outside the regions, or machine code: R8). loads holds the most recent
    // data loads of that run, oldest first, each {pc, effective address, size, value loaded}. Return the vector with
    // one input changed (a pointer that was not valid made valid), or null to give up. Addresses are 32 bits: a
    // register's or a doubleword's pointer is its low word.
    Vec repairVec(Vec v, long faultAddr, boolean write, List<long[]> loads) {
        for (int i = loads.size() - 1; i >= 0; i--) {
            long[] ld = loads.get(i);
            long d = faultAddr - (ld[3] & 0xffffffffL);
            if ((ld[2] != 4 && ld[2] != 8) || !genInRam(ld[1]) || d < -0x100 || d >= GEN_MAX_OFFSET
                || v.genPoked.containsKey(ld[1])) {
                continue;
            }
            genPoke(v, ld[1], genChunk(v, d), (int) ld[2]);
            return v;
        }
        for (int i = 0; i < 8; i++) {
            long d = faultAddr - (v.r[i] & 0xffffffffL);
            if (d < -0x100 || d >= GEN_MAX_OFFSET || genOwned(v.r[i])) continue;
            v.r[i] = genChunk(v, d);
            return v;
        }
        return null;
    }
}
