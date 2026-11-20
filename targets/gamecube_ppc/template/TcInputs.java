// Test-input generation for the GameCube PowerPC target: registers, scratch memory, scripted call results,
// per-function hints and pointer repair. Extended by TcOracle (locked), which runs the ORIGINAL code on these inputs.
//
// This is the only oracle file the watcher agent may change. It decides inputs only: whatever it generates, the
// expected results still come from running the original machine code. Rules (core/javalint.py): the only import is
// java.util.*; no I/O, reflection, threads, lambdas, Ghidra API or script state; only allow-listed library calls; new
// top-level members are named gen*, Gen* or GEN_*; never assign rnd; never try to tell dev and hidden vectors apart.
// Guest memory is big endian: every word a patch holds is written most significant byte first.
import java.util.*;

public abstract class TcInputs extends TcBase {
    static final long GEN_HEAP_LO = 0x81001000L;
    static final long GEN_HEAP_HI = 0x81040000L;
    static final int GEN_CHUNK = 0x400;
    static final int GEN_MAX_OFFSET = 0x4000;
    static final int GEN_BLIND = 3;  // repairs a vector may spend on a value that explains no address (repairVec)

    static class Vec {
        long[] r = new long[8];       // r3..r10
        long[] f = new long[8];       // f1..f8, the 64-bit register contents (IEEE double bits)
        long[] rets = new long[16];   // what mocked call k leaves in r3
        long[] frets = new long[16];  // and in f1 (double bits)
        List<Long> patchAddr = new ArrayList<>();
        List<byte[]> patchData = new ArrayList<>();
        long genHeapNext = GEN_HEAP_LO;
        Map<Long, Long> genPoked = new HashMap<>();
        int genBlind;                 // repairVec's repairs that explained no address, GEN_BLIND at most
        Set<Long> genNulled = new HashSet<>();  // words of our chunks repairVec set to 0 and may still make a pointer
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

    void genPut(byte[] b, int at, long w) {
        for (int k = 0; k < 4; k++) b[at + k] = (byte) (w >> (24 - 8 * k));
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
        for (int i = 0; i < 8; i++) v.f[i] = genSingleDouble();
        for (int i = 0; i < 16; i++) {
            int k = rnd.nextInt(10);
            v.rets[i] = k < 4 ? rnd.nextInt(4) : (k < 6 ? scrPtr() : (k < 8 ? 0 : rnd.nextInt() & 0xffffffffL));
            v.frets[i] = genDouble();
        }
        int stackWords = 0;
        for (int i = 0; i < proto.params.size(); i++) {
            Param q = proto.params.get(i);
            boolean ptr = proto.paramIsPointer.get(i);
            if (q.storage.matches("r([3-9]|10)")) {
                long iv = intArg(ptr);
                if (flavor == 3 && !ptr && rnd.nextInt(100) < 70) iv = 1 + rnd.nextInt(12);
                v.r[Integer.parseInt(q.storage.substring(1)) - 3] = q.kind.equals("f32") ? niceFloat() & 0xffffffffL : iv;
            } else if (q.storage.matches("f[1-8]")) {
                v.f[Integer.parseInt(q.storage.substring(1)) - 1] = q.kind.equals("f64") ? genDouble() : genSingleDouble();
            } else if (q.storage.equals("stack")) {
                stackWords = Math.max(stackWords, (q.stackOffset - 8) / 4 + 1);
            }
        }
        if (stackWords > 0) {
            byte[] st = new byte[stackWords * 4];
            for (int i = 0; i < stackWords; i++) genPut(st, i * 4, intArg(false));
            v.patchAddr.add(SP + 8);
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
    //                         ("any" picks a random word, "float" a random float's bits, "ptr" a fresh chunk)
    //   genret N VAL          mocked call N (0-based) leaves VAL in r3 ("sentinel": the oracle's return address)
    //   genfret N HEX64       and HEX64 (double bits) in f1
    // LOC is rN, retN (mocked call N's r3 result, 0-based, N < 16) or 0xADDR (a global), optionally followed by +0xOFF
    // steps; each step dereferences the pointer before it:
    // "r4+0x1c" is the word at r4+0x1c, "0x80346720+0x10" the word at *(0x80346720)+0x10.
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
                v.rets[n] = t[2].equals("sentinel") ? SENTINEL : genNum(t[2]) & 0xffffffffL;
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
            long x = w.equals("any") ? word() : w.equals("float") ? niceFloat() & 0xffffffffL
                : w.equals("ptr") ? genChunk(v, 0) : genNum(w);
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
        } else if (p[0].matches("ret\\d+")) {  // a call result: its pointer is set like a register's
            int n = Integer.parseInt(p[0].substring(3));
            if (n >= v.rets.length) throw new IllegalArgumentException("bad call index: " + loc);
            if (p.length == 1) {
                v.rets[n] = val;
                return;
            }
            if (!genOwned(v.rets[n])) v.rets[n] = genChunk(v, 0);
            slot = v.rets[n] + genNum(p[1]);
        } else {
            slot = genNum(p[0]);
            if (p.length > 1) slot = genDeref(v, slot) + genNum(p[1]);
        }
        for (int k = 2; k < p.length; k++) slot = genDeref(v, slot) + genNum(p[k]);
        genPoke(v, slot, val);
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
        genPut(d, 0, val);
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
    // Called after a run faulted at faultAddr (outside RAM, or machine code: R8). loads holds the most recent data
    // loads of that run, oldest first, each {pc, effective address, size, value loaded}. Return the vector with one
    // input changed (a pointer that was not valid made valid), or null to give up.
    // Every repair re-runs the vector and TcVectors allows 12, so the pointer repair below spends them carefully (Skies
    // 2026-11-13, about 26 functions out of repairs on chains and arrays of our own chunks, whose words are mostly
    // 0..15): a word the run wrote itself is no input (genOwnWrite); a word that one instruction read at a stride
    // through our patch is an array element, and the whole array gets one shared chunk in one repair; a small word of
    // our own chunk is tried as a null pointer first (the code may test it), and made a chunk if it is read again.
    Vec repairVec(Vec v, long faultAddr, boolean write, List<long[]> loads) {
        for (int i = loads.size() - 1; i >= 0; i--) {
            long[] ld = loads.get(i);
            long d = (int) (faultAddr - ld[3]);   // 32-bit: a null pointer with a negative offset wraps to 0xffffxxxx
            if (ld[2] != 4 || !genInRam(ld[1]) || d < -0x100 || d >= GEN_MAX_OFFSET || genOwnWrite(v, ld)) continue;
            if (v.genPoked.containsKey(ld[1])) {
                if (!v.genNulled.contains(ld[1])) continue;
                v.genNulled.remove(ld[1]);        // set to 0 last time and still the pointer: a real chunk now
                genPoke(v, ld[1], genChunk(v, d));
                return v;
            }
            long end = genPatchEnd(v, ld[1]);
            long s = genStride(loads, ld);
            if (end > 0 && s > 0) {
                long lo = ld[1];
                while (genLoadedBy(loads, ld[0], lo - s) && genPatchEnd(v, lo - s) == end) lo -= s;
                long p = genChunk(v, d);
                for (int j = 0; j < 64 && lo + j * s + 4 <= end; j++) {
                    long a = lo + j * s;
                    if (v.genPoked.containsKey(a) || genOwnWriteAt(v, loads, a)) continue;
                    genPoke(v, a, p);
                }
                return v;
            }
            if (ld[3] >= 1 && ld[3] <= 15 && ld[1] >= GEN_HEAP_LO && ld[1] < v.genHeapNext && genPatched(v, ld[1])) {
                genPoke(v, ld[1], 0);
                v.genNulled.add(ld[1]);
                return v;
            }
            genPoke(v, ld[1], genChunk(v, d));
            return v;
        }
        for (int i = 0; i < 8; i++) {
            long d = (int) (faultAddr - v.r[i]);
            if (d < -0x100 || d >= GEN_MAX_OFFSET || genOwned(v.r[i])) continue;
            v.r[i] = genChunk(v, d);
            return v;
        }
        // No single value is near the fault: the address may be a sum (base + scaled index). The newest large value that
        // is not one of our pointers becomes a small index, but only a value that explains the address (the fault less
        // it, at an element size, is a RAM base: genSumBase) and only a word this vector set (genPatched: never the
        // image's constants, which no C reads as an input). Every repair re-runs the vector, and one that explains
        // nothing spends it: Skies 2026-11-12, an f32 constant at 8034b0ac made 0..15 while a scripted call result was
        // the pointer (8017cc70: 51 valid vectors to 25), and every store word of a loop run off RAM's end, each re-run
        // to the step limit (80239108: out of time).
        for (int i = loads.size() - 1; i >= 0; i--) {
            long[] ld = loads.get(i);
            if (ld[2] != 4 || !genPatched(v, ld[1]) || genNotIndex(ld[3]) || !genSumBase(faultAddr, ld[3])
                || v.genPoked.containsKey(ld[1])) {
                continue;
            }
            genPoke(v, ld[1], rnd.nextInt(16));
            return v;
        }
        for (int i = 0; i < 8; i++) {
            if (genNotIndex(v.r[i]) || !genSumBase(faultAddr, v.r[i])) continue;
            v.r[i] = rnd.nextInt(16);
            return v;
        }
        // No value explains the address. A scripted call result near it is the pointer: TcVectors.repair's (null).
        for (long ret : v.rets) {
            long d = faultAddr - ret;
            if (d >= -0x100 && d < GEN_MAX_OFFSET && !genOwned(ret)) return null;
        }
        // Otherwise a large value can still be the cause without being a term of the address: a count (Skies 801559bc:
        // the word at r3 counts a loop of stores, which run off RAM's end) or one term of a sum whose other term is
        // wrong too (8019ae84: a table base and its index both unset, no base in RAM whichever is taken away). The
        // newest such word this vector set, read once in the log (a word read by the same instruction again is a
        // loop's data, 80239108), or else a register, becomes small. Such a repair is a guess, so a vector gets
        // GEN_BLIND of them, not the 12 that d7eeeada's fallback could spend. The guess is the fixed value 1, so it draws
        // nothing from rnd and every later vector is the one it would have been. A fault at or near address 0 is a null
        // pointer, never a count or a sum run off RAM's end: no guess is made for it.
        if (v.genBlind >= GEN_BLIND || genNearNull(faultAddr)) return null;
        for (int i = loads.size() - 1; i >= 0; i--) {
            long[] ld = loads.get(i);
            if (ld[2] != 4 || !genPatched(v, ld[1]) || genNotIndex(ld[3]) || genReadAt(loads, ld[0]) > 1
                || v.genPoked.containsKey(ld[1])) {
                continue;
            }
            v.genBlind++;
            genPoke(v, ld[1], 1);
            return v;
        }
        for (int i = 0; i < 8; i++) {
            if (genNotIndex(v.r[i])) continue;
            v.genBlind++;
            v.r[i] = 1;
            return v;
        }
        return null;
    }

    // Within GEN_MAX_OFFSET above address 0, or 64 KiB below it (a null pointer with a negative offset wraps).
    boolean genNearNull(long a) {
        long s = (int) a;
        return s > -0x10000 && s < GEN_MAX_OFFSET;
    }

    // The run wrote the word it loaded: our patches hold another value there, so poking it changes nothing.
    boolean genOwnWrite(Vec v, long[] ld) {
        long pv = genPatchedValue(v, ld[1]);
        return pv >= 0 && pv != (ld[3] & 0xffffffffL);
    }

    boolean genOwnWriteAt(Vec v, List<long[]> loads, long addr) {
        for (long[] ld : loads) if (ld[2] == 4 && ld[1] == addr && genOwnWrite(v, ld)) return true;
        return false;
    }

    // The word our patches put at addr (the last patch holding all of it wins), or -1 when no patch does.
    long genPatchedValue(Vec v, long addr) {
        long w = -1;
        for (int i = 0; i < v.patchAddr.size(); i++) {
            long lo = v.patchAddr.get(i);
            byte[] b = v.patchData.get(i);
            if (addr < lo || addr + 4 > lo + b.length) continue;
            int o = (int) (addr - lo);
            w = 0;
            for (int k = 0; k < 4; k++) w = (w << 8) | (b[o + k] & 0xff);
        }
        return w;
    }

    // The end of the largest patch holding the word at addr, or -1.
    long genPatchEnd(Vec v, long addr) {
        long end = -1;
        for (int i = 0; i < v.patchAddr.size(); i++) {
            long lo = v.patchAddr.get(i);
            long hi = lo + v.patchData.get(i).length;
            if (addr >= lo && addr + 4 <= hi && hi > end) end = hi;
        }
        return end;
    }

    // The stride at which the instruction that made load ld also read the words below it (an array walked by a loop):
    // the smallest whole-word distance down to another of its 4-byte loads, at most GEN_CHUNK; 0 when there is none.
    long genStride(List<long[]> loads, long[] ld) {
        long s = 0;
        for (long[] o : loads) {
            long k = ld[1] - o[1];
            if (o[0] != ld[0] || o[2] != 4 || k <= 0 || k % 4 != 0 || k > GEN_CHUNK) continue;
            if (s == 0 || k < s) s = k;
        }
        return s;
    }

    boolean genLoadedBy(List<long[]> loads, long pc, long addr) {
        for (long[] ld : loads) if (ld[0] == pc && ld[2] == 4 && ld[1] == addr) return true;
        return false;
    }

    // How many of the logged loads the instruction at pc made.
    int genReadAt(List<long[]> loads, long pc) {
        int n = 0;
        for (long[] ld : loads) if (ld[0] == pc) n++;
        return n;
    }

    // Already a small index, or a pointer into RAM (ours or the game's): not what made the sum land outside RAM.
    boolean genNotIndex(long x) {
        return x < 0x100 || genOwned(x) || genInRam(x);
    }

    // The fault is base + x * size for an element size of 1 to 64 bytes and a base in RAM (a field offset past it
    // allowed): x can be the index.
    boolean genSumBase(long faultAddr, long x) {
        for (int k = 0; k <= 6; k++) {
            long base = (faultAddr - (x << k)) & 0xffffffffL;
            if (base >= RAM_LO && base < RAM_HI + GEN_MAX_OFFSET) return true;
        }
        return false;
    }

    // The word at addr lies in one of this vector's patches (scratch, chunks, named globals, stack arguments).
    boolean genPatched(Vec v, long addr) {
        for (int i = 0; i < v.patchAddr.size(); i++) {
            long lo = v.patchAddr.get(i);
            if (addr >= lo && addr + 4 <= lo + v.patchData.get(i).length) return true;
        }
        return false;
    }
}
