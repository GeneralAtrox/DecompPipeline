// What a mocked call leaves besides r3 and f1, and the oracle's last resorts (Skies of Arcadia round r9). Pure helpers:
// TcOracle and TcVectors hold the state, so this file needs no Ghidra and tests/test_gc_callfx.py runs it.
// LOCKED like the oracle. Not a script itself.
//  - Scripted r4 (vectors.txt "Q k word ..."): a callee that sets r4 on every path (TcFlow.returnsR4) returns r3:r4.
//    Its r4 was clobber's 0 while vectors were chosen and the strict replay's junk in the harness (TcStrict), so a loop
//    that runs until the 64-bit value is 0 ended in the choice and never in the replay (MSL __ull2dec 8025baf4 and
//    longlong2str 8025e14c divide by 10 through __div2u: 1 and 0 of 96 vectors). Call k's r4 after such a callee is
//    r4(words, k): the vector's own 16 words (r4Words, a generator of its own, so no other input changes), 0 after,
//    in both runs; Q lists every such call below TcOracle.RETS and tc_lastr4 returns its word (else junk or 0).
//  - genretptr CALLEE [OFF [PTR..]] (a hint, TcVectors.retPtrHints): every call to CALLEE returns a fresh chunk
//    (grow) whose words at PTR.. are scratch pointers (Skies 80131a04 stores through the result's +4 word), for
//    loops whose allocator results are all stored through, past what retN hints (16) and lateRepair's 16 per repair
//    reach (Skies 80197270: 75 calls, 80195f9c: about 400).
//  - Last resorts (TcVectors.lastResort): only for a run the repairs gave up on, so every vector they kept is as before.
//    tableIndex: a word of an image table, randomised as a global, steered an access out of RAM (8008fc50's store
//    offsets at 802dff1c) or a switch out of the body (800f2464, 80148024): the globals near it get the image's bytes.
//    Callee writes (vectors.txt "K call addr bytes"): mocked call `call`
//    stores the bytes at addr once it returns, in the oracle and in the C runtime alike (tc_fx.h): a pointer the callee
//    fills in at a word the function stored before the call and then dereferenced (800a40b0: 800a3ed4 sets 0x38 of the
//    record the caller passes), or a flag an interrupt sets while the function polls it (polled: 8028532c waits on
//    80347df0 after 8028550c, 802846e4 on a request count after 8023559c).
import java.util.*;

final class TcCallFx {
    private TcCallFx() {
    }

    static final int POLL = 32;  // a poll: the run's last POLL loads were one word, read by one instruction

    // A vector's 16 scripted r4 words: a third each 0, a small number, any word.
    static long[] r4Words(long seed, int id) {
        Random h = new Random(seed * 43 + id * 0x5851f42dL + 0x7234);
        long[] w = new long[16];
        for (int i = 0; i < 16; i++) {
            int k = h.nextInt(3);
            w[i] = k == 0 ? 0 : k == 1 ? h.nextInt(16) : h.nextInt() & 0xffffffffL;
        }
        return w;
    }

    static long r4(long[] words, int k) {
        return words != null && k < words.length ? words[k] : 0;
    }

    // vectors.txt Q: "Q k word ..." (k decimal, word hex) for the calls whose r4 was scripted, or "".
    static String qLine(List<long[]> r4s) {
        if (r4s.isEmpty()) return "";
        StringBuilder b = new StringBuilder("Q");
        for (long[] e : r4s) b.append(' ').append(e[0]).append(' ').append(String.format("%08x", e[1]));
        return b.append('\n').toString();
    }

    // vectors.txt K: one "K call addr bytes" line per callee write {call, addr, size, value} (big endian), or "".
    static String kLines(List<long[]> fx) {
        StringBuilder b = new StringBuilder();
        for (long[] e : fx == null ? List.<long[]>of() : fx) {
            b.append("K ").append(e[0]).append(' ').append(String.format("%08x ", e[1]));
            for (byte x : bytes(e)) b.append(String.format("%02x", x & 0xff));
            b.append('\n');
        }
        return b.toString();
    }

    static byte[] bytes(long[] e) {
        byte[] d = new byte[(int) e[2]];
        for (int i = 0; i < d.length; i++) d[i] = (byte) (e[3] >>> (8 * (d.length - 1 - i)));
        return d;
    }

    static boolean has(List<long[]> fx, long call, long addr) {
        if (fx != null) for (long[] e : fx) if (e[0] == call && e[1] == addr) return true;
        return false;
    }

    static boolean inRam(long a) {
        return a >= TcBase.RAM_LO && a < TcBase.RAM_HI;
    }

    // The fault is base + (x << k) for a base in RAM (a field offset past its end allowed), k 0..6, and the image's
    // word o there puts the access back inside RAM.
    static boolean tableIndex(long fault, long x, long o) {
        if (x == o) return false;
        for (int k = 0; k <= 6; k++) {
            long base = (fault - (x << k)) & 0xffffffffL;
            if (base < TcBase.RAM_LO || base >= TcBase.RAM_HI + 0x4000) continue;
            if (inRam((base + (o << k)) & 0xffffffffL)) return true;
        }
        return false;
    }

    // The run's last POLL loads were one address, size, value and instruction (a loop waiting on a word): that load,
    // else null.
    static long[] polled(List<long[]> loads) {
        if (loads.size() < POLL) return null;
        long[] last = loads.get(loads.size() - 1);
        for (int i = loads.size() - POLL; i < loads.size(); i++) {
            long[] ld = loads.get(i);
            if (ld[0] != last[0] || ld[1] != last[1] || ld[2] != last[2] || ld[3] != last[3]) return null;
        }
        return last;
    }

    // genretptr: mocked call k to a hinted callee (s: its hint's {OFF, PTR..}, null for any other callee) returns a
    // fresh chunk sized for a field at OFF, its words at PTR.. scratch pointers, set as its scripted result (Vec.rets,
    // else lateOf) unless it is one of ours already. The index of the chunk's first patch, which runOracle puts in
    // memory at once; -1 when nothing changed.
    static int grow(TcOracle o, TcInputs.Vec v, int k, long[] s) {
        if (s == null || k >= TcOracle.RETS || o.genOwned(TcOracle.ret(v, k))) return -1;
        int n = v.patchAddr.size();
        long c = o.genChunk(v, s[0]);
        for (int i = 1; i < s.length && v.patchAddr.size() > n; i++) {  // none when the heap ran out (a scratch pointer)
            long at = c - v.patchAddr.get(n) + s[i];
            if (at >= 0 && at + 4 <= v.patchData.get(n).length) o.genPut(v.patchData.get(n), (int) at, o.scrPtr());
        }
        if (k < v.rets.length) v.rets[k] = c;
        else TcOracle.lateOf.computeIfAbsent(v, x -> new long[TcOracle.RETS - v.rets.length])[k - v.rets.length] = c;
        return n;
    }

    // What the callee leaves in a polled word: 0 becomes 1, anything else 0.
    static long flip(long x) {
        return x == 0 ? 1 : 0;
    }
}
