// Test-input generation: registers, scratch memory, scripted call returns, per-function hints and
// pointer repair. Extended by TcOracle (locked), which runs the ORIGINAL code on these inputs.
//
// This is the only oracle file the watcher agent may change. It decides inputs only: whatever it
// generates, the expected results still come from running the original machine code. Rules, enforced
// by DecompPipeline core/javalint.py before any change is compiled or promoted:
// - the only import is java.util.*; no I/O, reflection, threads, lambdas, Ghidra API or script state;
// - only allow-listed library calls; new top-level members are named gen*, Gen* or GEN_*;
// - never assign rnd (TcPrepare seeds it); never try to tell dev and hidden vectors apart.
import java.util.*;

public abstract class TcInputs extends TcBase {
    static final long GEN_HEAP_LO = 0x8cc01000L;
    static final long GEN_HEAP_HI = 0x8cc40000L;
    static final int GEN_CHUNK = 0x400;
    static final int GEN_MAX_OFFSET = 0x4000;

    static class Vec {
        long[] r = new long[4];
        int[] fr = new int[8];
        int[] xf = new int[16];
        long[] rets = new long[16];
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

    long scrPtr() {
        return SCR + 4L * rnd.nextInt((int) (SCR_SIZE / 4));
    }

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

    byte[] scratchBytes(int flavor) {
        byte[] b = new byte[(int) SCR_SIZE];
        if (flavor == 1) {
            String alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.\\/:;, ;;-";
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
            // flavor 3: pointer-rich, so pointer-to-pointer arguments reach real code paths
            int k = rnd.nextInt(100);
            long w = flavor != 3 ? word() : k < 55 ? scrPtr() : k < 80 ? niceFloat() & 0xffffffffL : rnd.nextInt(16);
            b[i] = (byte) w;
            b[i + 1] = (byte) (w >> 8);
            b[i + 2] = (byte) (w >> 16);
            b[i + 3] = (byte) (w >> 24);
        }
        return b;
    }

    // ---------- one vector ----------
    // flavor 0 words, 1 text, 2 small bytes, 3 pointer-rich
    Vec makeVec(int flavor, Proto proto, List<Long> globals) {
        Vec v = new Vec();
        v.patchAddr.add(SCR);
        v.patchData.add(scratchBytes(flavor));
        for (int i = 0; i < 4; i++) v.r[i] = intArg(false);
        for (int i = 0; i < 8; i++) v.fr[i] = niceFloat();
        for (int i = 0; i < 16; i++) v.xf[i] = niceFloat();
        for (int i = 0; i < 16; i++) {
            int k = rnd.nextInt(10);
            v.rets[i] = k < 4 ? rnd.nextInt(4) : (k < 6 ? scrPtr() : (k < 8 ? 0 : rnd.nextInt() & 0xffffffffL));
        }
        int stackWords = 0;
        for (int i = 0; i < proto.params.size(); i++) {
            Param q = proto.params.get(i);
            boolean ptr = proto.paramIsPointer.get(i);
            if (q.storage.matches("r[4-7]")) {
                long iv = intArg(ptr);
                // pointer-rich vectors get small positive counts so loops over pointed-to data actually run
                if (flavor == 3 && !ptr && rnd.nextInt(100) < 70) iv = 1 + rnd.nextInt(12);
                v.r[Integer.parseInt(q.storage.substring(1)) - 4] = q.kind.equals("f32") ? niceFloat() : iv;
            } else if (q.storage.matches("fr([4-9]|1[01])")) {
                v.fr[Integer.parseInt(q.storage.substring(2)) - 4] = niceFloat();
            } else if (q.storage.equals("stack")) {
                stackWords = Math.max(stackWords, q.stackOffset / 4 + 1);
            }
        }
        if (stackWords > 0) {
            byte[] st = new byte[stackWords * 4];
            for (int i = 0; i < stackWords; i++) {
                long w = intArg(false);
                for (int k = 0; k < 4; k++) st[i * 4 + k] = (byte) (w >> (8 * k));
            }
            v.patchAddr.add(SP);
            v.patchData.add(st);
        }
        for (long g : globals) {
            if (rnd.nextInt(10) >= 7) continue;
            long a = g & ~3L;
            byte[] d = new byte[32];
            for (int i = 0; i < 32; i += 4) {
                long w = word();
                for (int k = 0; k < 4; k++) d[i + k] = (byte) (w >> (8 * k));
            }
            v.patchAddr.add(a);
            v.patchData.add(d);
        }
        return v;
    }

    // ---------- hints: oracle/hints/<addr>.txt, applied to every vector of that function ----------
    //   ptr LOC          the word at LOC holds a pointer to a fresh chunk of pointer-rich memory
    //   range rN LO HI   rN is uniform in [LO, HI] (decimal or 0x hex); "genrange LOC LO HI" sets the
    //                    word at LOC (same LOC syntax as ptr) the same way
    //   gentopbyte ADDR LEN B PCT   fill the words in [ADDR, ADDR+LEN) (e.g. a caller stack buffer that a
    //                    stubbed callee would fill): each word's top byte is B with PCT% chance, else another
    //                    value; at least one word before the last gets B, and the last word never does
    //   genret N VAL     call N (0-based) returns VAL in r0/fr0, e.g. a callee that really preserves an r0
    //                    holding the caller's saved PR (VAL "sentinel" is the oracle's return address)
    //   genretfn 0xCALLEE LO HI   every call to CALLEE (any call number up to the 64th) returns a value drawn from
    //                    LO..HI, e.g. a table index; the locked TcLate takes these lines, so applyHints never sees them
    // LOC is rN or 0xADDR (a global), optionally followed by +0xOFF steps; each step dereferences the
    // pointer before it: "r4+0x10c" is the word at r4+0x10c, "0x8c0c78a0+0x1c8" the word at *(0x8c0c78a0)+0x1c8.
    void applyHints(Vec v, List<String> hints) {
        for (String line : hints) {
            String s = line.trim();
            if (s.isEmpty() || s.startsWith("#")) continue;
            String[] t = s.split("\\s+");
            if (t[0].equals("range") && t.length == 4 && t[1].matches("r[4-7]")) {
                long lo = genNum(t[2]);
                long hi = genNum(t[3]);
                long span = Math.max(1, hi - lo + 1);
                v.r[Integer.parseInt(t[1].substring(1)) - 4] = (lo + (long) (rnd.nextDouble() * span)) & 0xffffffffL;
            } else if (t[0].equals("genrange") && t.length == 4) {
                long lo = genNum(t[2]);
                long span = Math.max(1, genNum(t[3]) - lo + 1);
                genPlace(v, t[1], (lo + (long) (rnd.nextDouble() * span)) & 0xffffffffL);
            } else if (t[0].equals("gentopbyte") && t.length == 5) {
                genTopByte(v, genNum(t[1]), (int) genNum(t[2]), (int) genNum(t[3]), (int) genNum(t[4]));
            } else if (t[0].equals("genret") && t.length == 3) {
                int n = (int) genNum(t[1]);
                if (n < 0 || n >= v.rets.length) throw new IllegalArgumentException("bad genret index: " + s);
                v.rets[n] = t[2].equals("sentinel") ? SENTINEL : genNum(t[2]) & 0xffffffffL;
            } else if (t[0].equals("ptr") && t.length == 2) {
                genSetPtr(v, t[1]);
            } else if (t[0].equals("genscript") && t.length >= 5) {
                long[] vals = new long[t.length - 4];
                for (int i = 0; i < vals.length; i++) vals[i] = genNum(t[4 + i]);
                genScript(v, t[1], (int) genNum(t[2]), genNum(t[3]), vals);
            } else {
                throw new IllegalArgumentException("bad hint line: " + s);
            }
        }
    }

    void genTopByte(Vec v, long addr, int len, int b, int pct) {
        int n = len / 4;
        if (n < 2 || !genInRam(addr) || !genInRam(addr + 4L * (n - 1))) throw new IllegalArgumentException("bad gentopbyte range");
        byte[] d = new byte[n * 4];
        int forced = rnd.nextInt(n - 1);
        for (int i = 0; i < n; i++) {
            boolean hit = i != n - 1 && (i == forced || rnd.nextInt(100) < pct);
            int top = b & 0xff;
            while (!hit && top == (b & 0xff)) {
                int k = rnd.nextInt(4);
                top = k == 0 ? 0 : k == 1 ? 0xff : k == 2 ? rnd.nextInt(8) : rnd.nextInt(256);
            }
            long w = ((long) top << 24) | rnd.nextInt(0x1000000);
            for (int k = 0; k < 4; k++) d[i * 4 + k] = (byte) (w >> (8 * k));
        }
        v.patchAddr.add(addr);
        v.patchData.add(d);
    }

    long genNum(String s) {
        return s.startsWith("0x") ? Long.parseLong(s.substring(2), 16) : Long.parseLong(s);
    }

    void genSetPtr(Vec v, String loc) {
        genPlace(v, loc, genChunk(v, 0));
    }

    // Store val at LOC (see the hint format above); a bare rN receives val itself.
    void genPlace(Vec v, String loc, long val) {
        String[] p = loc.split("\\+");
        long slot;
        if (p[0].matches("r[4-7]")) {
            int i = Integer.parseInt(p[0].substring(1)) - 4;
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
        genPoke(v, slot, val);
    }

    //   genscript LOC N END V1 V2 ...   LOC points to a fresh halfword stream (a bytecode script): 0..N
    //                    halfwords each picked from V1.., then END
    void genScript(Vec v, String loc, int n, long end, long[] vals) {
        long base = genChunk(v, 2L * n + 2);
        int len = rnd.nextInt(n + 1);
        byte[] d = new byte[2 * len + 2];
        for (int i = 0; i <= len; i++) {
            long h = i == len ? end : vals[rnd.nextInt(vals.length)];
            d[2 * i] = (byte) h;
            d[2 * i + 1] = (byte) (h >> 8);
        }
        v.patchAddr.add(base);
        v.patchData.add(d);
        genPlace(v, loc, base);
    }

    boolean genOwned(long a) {
        return (a >= SCR && a < SCR + SCR_SIZE) || (a >= GEN_HEAP_LO && a < GEN_HEAP_HI);
    }

    long genDeref(Vec v, long slot) {
        Long have = v.genPoked.get(slot);
        if (have != null) return have;
        long p = genChunk(v, 0);
        genPoke(v, slot, p);
        return p;
    }

    boolean genInRam(long a) {
        return a >= RAM_LO && a + 4 <= RAM_HI;
    }

    void genPoke(Vec v, long addr, long val) {
        if (!genInRam(addr)) throw new IllegalArgumentException("cannot set a word outside RAM: " + hex(addr));
        byte[] d = new byte[4];
        for (int k = 0; k < 4; k++) d[k] = (byte) (val >> (8 * k));
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
            // mostly small numbers: a word later used as a pointer is repaired on demand, while a
            // pointer-sized loop count would only run the function into the step limit
            long w = k < 10 ? scrPtr() : k < 60 ? rnd.nextInt(16) : k < 75 ? niceFloat() & 0xffffffffL : 0;
            for (int j = 0; j < 4; j++) d[i + j] = (byte) (w >> (8 * j));
        }
        v.patchAddr.add(base);
        v.patchData.add(d);
        return base + lead;
    }

    // ---------- pointer repair ----------
    // Called by TcPrepare after a run faulted at faultAddr (outside RAM). loads holds the most recent
    // mov.b/w/l loads of that run, oldest first, each {pc, effective address, size, value loaded}.
    // Return the vector with one input changed (a pointer that was not valid made valid), or null to give up.
    Vec repairVec(Vec v, long faultAddr, boolean write, List<long[]> loads) {
        for (int i = loads.size() - 1; i >= 0; i--) {
            long[] ld = loads.get(i);
            long d = faultAddr - ld[3];
            // only a word the vector can set: inside RAM (this also skips the faulting load itself)
            if (ld[2] != 4 || !genInRam(ld[1]) || d < -0x100 || d >= GEN_MAX_OFFSET || v.genPoked.containsKey(ld[1])) {
                continue;
            }
            genPoke(v, ld[1], genChunk(v, d));
            return v;
        }
        for (int i = 0; i < 4; i++) {
            long d = faultAddr - v.r[i];
            if (d < -0x100 || d >= GEN_MAX_OFFSET || genOwned(v.r[i])) continue;
            v.r[i] = genChunk(v, d);
            return v;
        }
        return null;
    }
}
