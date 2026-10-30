// Call targets from the inputs (batch 28). A mocked call goes wherever the run's register says: a function pointer
// the function reads from its input memory, an argument register or a scripted call result. A random word there is a
// call to an address with no memory on the Dreamcast (the game would crash on that input), and in the readable-C
// runner, whose guest RAM is the only memory it traps calls into, such an address can be a host DLL's code: the
// readable function then runs it natively and crashes, or worse. So a call to a target outside RAM that is no constant
// of the body is an invalid input, repaired like a fault: the word holding it becomes a fresh chunk pointer (data in
// RAM: the oracle never runs a mocked callee, and the readable runner traps and logs a call there). NULL stays a valid
// target (a pointer legitimately holds it; both judges trap a call to it). Constant targets are unchanged.
//
// Run flags (project.json "run_flags", core/compare.run_flag): a function whose loop runs while a word it sets itself
// is non-zero, and only a callee clears it (Power Stone 2 Game_Main 8c02d2a8 sets 8c5423d4 to 1 and loops while it
// is; 8c02d930 -> 8c05b54e -> 8c05b32a clears it). Mocked callees store nothing, so every vector ran to the call
// limit. Opt-in per function with evidence: script arg 12 "rf:WORD_CALLEE_N" (TcPrepare sets `flag`). At the Nth
// mocked call (from 1) to CALLEE in a run, after TcStrict.call, the 4 bytes at WORD become 0, as the callee's own
// store: TcExit logs it like any write, Stored does not count it the function's. Only a word wholly in RAM with no
// instruction byte (TcStrict.writable). The runtimes do the same at their tc_call (vectors.txt header line
// "f WORD CALLEE N"; tc_rt.c, rd_veh.c), so xemu, the translator's C and the port form see it too. meta "run_flag".
// The settable form (core/run_flag_set; script arg 12 "rs:OFFSET_CALLEE_N_REG_BITS"): the Nth call sets BITS in the
// word at OFFSET past rREG's value at the function's entry (TcOracle.setEntry hands it to `entry`), read at that call:
// Toy Commander's window loop 8c077c30 waits for bit 0x400 of *(r4 + 0x5c), which a window callback sets. Header line
// "f OFFSET CALLEE N REG BITS" (the runners work out each vector's word), meta "run_flag" with "base" and "bits".
import ghidra.app.emulator.EmulatorHelper;
import java.util.*;

final class TcCalls {
    private TcCalls() {
    }

    static long[] flag;  // {word, callee, n} of the function being prepared (folded), or null: none listed
                         // the settable form: {offset, callee, n, reg, bits}
    static final long[] entry = new long[4];  // r4..r7 at the entry of the vector being run

    static long entry(int i, long v) {
        entry[i] = v;
        return v;
    }

    static long[] parseFlag(String a) {
        if (a != null && a.startsWith("rs:")) {
            String[] p = a.substring(3).split("_");
            if (p.length != 5) throw new IllegalArgumentException("run_flags arg: " + a);
            long n = Long.parseLong(p[2]), reg = Long.parseLong(p[3]);
            if (n < 1 || reg < 4 || reg > 7) throw new IllegalArgumentException("run_flags arg: " + a);
            return new long[] {Long.parseLong(p[0], 16), TcBase.fold(Long.parseLong(p[1], 16)), n, reg,
                Long.parseLong(p[4], 16)};
        }
        if (a == null || !a.startsWith("rf:")) return null;
        String[] p = a.substring(3).split("_");
        if (p.length != 3) throw new IllegalArgumentException("run_flags arg: " + a);
        long n = Long.parseLong(p[2]);
        if (n < 1) throw new IllegalArgumentException("run_flags arg: call " + n);
        return new long[] {TcBase.fold(Long.parseLong(p[0], 16)), TcBase.fold(Long.parseLong(p[1], 16)), n};
    }

    // TcOracle, at each mocked call right after TcStrict.call: the Nth call to the listed callee clears the word.
    static void flagCall(TcOracle o, TcOracle.Result res, EmulatorHelper emu, long pc) {
        if (flag == null || TcBase.fold(pc) != flag[1]) return;
        long n = 0;
        for (String c : res.calls) if (Long.parseLong(c.substring(0, c.indexOf(' ')), 16) == flag[1]) n++;
        long w = flag.length == 5 ? TcBase.fold((entry[(int) flag[3] - 4] + flag[0]) & 0xffffffffL) : flag[0];
        if (n != flag[2] || !TcBase.inRam(w, 4)) return;
        for (int k = 0; k < 4; k++) if (o.facts.isCode(w + k)) return;
        byte[] b = new byte[4];  // the clearing form stores 0; the settable form sets its bits in the word as it is
        if (flag.length == 5) {
            b = emu.readMemory(o.toAddr(w), 4).clone();
            for (int k = 0; k < 4; k++) b[k] |= (byte) (flag[4] >>> (8 * k));
        }
        TcStrict s = res.strict;
        if (s != null && s.stored != null) s.stored.muted = true;  // not the function's own store (Stored)
        emu.writeMemory(o.toAddr(w), b);
        if (s != null && s.stored != null) s.stored.muted = false;
    }

    static String flagHeader() {
        if (flag != null && flag.length == 5)
            return "f " + TcBase.hex(flag[0]) + ' ' + TcBase.hex(flag[1]) + ' ' + flag[2] + ' ' + flag[3] + ' '
                + TcBase.hex(flag[4]) + '\n';
        return flag == null ? "" : "f " + TcBase.hex(flag[0]) + ' ' + TcBase.hex(flag[1]) + ' ' + flag[2] + '\n';
    }

    static String flagMeta() {
        if (flag != null && flag.length == 5)
            return ",\n  \"run_flag\": {\"base\": \"r" + flag[3] + "\", \"offset\": \"" + TcBase.hex(flag[0])
                + "\", \"bits\": \"" + TcBase.hex(flag[4]) + "\", \"callee\": \"" + TcBase.hex(flag[1]) + "\", \"call\": "
                + flag[2] + "}";
        return flag == null ? "" : ",\n  \"run_flag\": {\"word\": \"" + TcBase.hex(flag[0]) + "\", \"callee\": \""
            + TcBase.hex(flag[1]) + "\", \"call\": " + flag[2] + "}";
    }

    // A call to t (folded) is from the inputs and invalid: not NULL, outside guest RAM, not a constant of the body.
    static boolean fromInputs(long t, Set<Long> bodyConsts) {
        return t != 0 && (t < TcBase.RAM_LO || t >= TcBase.RAM_HI) && !bodyConsts.contains(t);
    }

    // The input word the target came from becomes a fresh chunk pointer: the latest logged 4-byte load of exactly that
    // value (a word in RAM the vector may set), else an entry register, else a scripted call result the run used.
    // Exact matches only (a small target equals many unrelated words). The repaired vector, or null.
    static TcInputs.Vec repair(TcVectors g, TcInputs.Vec v, long target, List<long[]> loads, int used) {
        for (int i = loads.size() - 1; i >= 0; i--) {
            long[] ld = loads.get(i);
            if (ld[2] != 4 || (ld[3] & 0xffffffffL) != target || !g.genInRam(ld[1]) || v.genPoked.containsKey(ld[1])) {
                continue;
            }
            g.genPoke(v, ld[1], g.genChunk(v, 0));
            return v;
        }
        for (int i = 0; i < 4; i++) {
            if (v.r[i] != target) continue;
            v.r[i] = g.genChunk(v, 0);
            return v;
        }
        long[][] regs = {g.entryLow, g.entryGi};
        boolean[][] which = {g.lowWhich, g.giWhich};
        for (int k = 0; k < 2; k++) {
            for (int i = 0; regs[k] != null && i < regs[k].length; i++) {
                if (!which[k][i] || regs[k][i] != target) continue;
                regs[k][i] = g.genChunk(v, 0);
                return v;
            }
        }
        for (int k = Math.min(used, v.rets.length) - 1; k >= 0; k--) {
            if (v.rets[k] != target) continue;
            v.rets[k] = g.genChunk(v, 0);
            return v;
        }
        return null;
    }
}
