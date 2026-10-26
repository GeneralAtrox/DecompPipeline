// Listed hardware ops (project.json "hw_ops", core/hw_ops.py): a function whose only parts C cannot express are single
// hardware operations the project lists with evidence. Not a script itself. TcPrepare reads script arg 11 and checks
// the body (check: every hardware op TcCatalog counts is a listed one, else the function stays unsupported); the
// oracle models each listed op, and the function is judged on everything else:
//   read R  a load from the hardware register R (a pool constant used as an address) returns the target's fixed
//           value for it (target.json "hw_reads": Spawn 8c04f4c0 reads the system bus revision 0xa05f689c, 0xB on a
//           retail Dreamcast). TcFold serves it; the C reads it with tc_hw_read32(R); vectors.txt header line
//           "Y R value". A store to R, unless also listed as a write, stays a write outside RAM (refused).
//           A register 1 or 2 bytes wide (target.json "hw_reads" width, "hw_read_sizes": Grandia 060398cc polls the
//           byte FTCSR at 0xfffffe11 for ICF, bit 7) holds its value in that many bytes from R; only a load inside
//           them is served, and the C reads it with tc_hw_read8(R) / tc_hw_read16(R). Its arg value and meta value
//           have 2 hex digits per byte, and its Y line ends with the width; a 4-byte register's are as before.
//   write R a store to the hardware register R is logged as a W line and compared, as the P4 cache-array stores are
//           (TcExit.p4, tc_p4.c): Spawn 8c058620 acknowledges interrupts at 0xa05f6900. The C stores with
//           tc_hw_write32(R, v) / tc_hw_write16(R, v); compare.named_words judges the register's words.
//   gbr     ldc/stc (and .l) of GBR: GBR is state, 0 at entry, compared at exit (expected.txt "GB value"); the C
//           writes it with sh4_ldc_gbr(v) and reads it with sh4_stc_gbr(); vectors.txt header line "Z".
//   bl      an ldc setting SR.BL (exceptions blocked: Spawn 8c0583b0 sets up the interrupt tables with them blocked).
//           Nothing raises an exception in the test run or the C, so TcSr allows it; SR.RB stays refused.
//   cache   ocbi / ocbp / ocbwb @Rn: no-ops in the test run (callbacks) and in C (sh4_ocbi, sh4_ocbp, sh4_ocbwb).
//           Memory is modelled coherent: what the cache would write back is already in RAM. ocbi on hardware drops a
//           dirty line instead; the evidence must say the line holds nothing the code wrote through the cache.
// Static per prepare, as TcBase.sqWindow: TcPrepare sets `on` for every prepare (null: nothing listed, as before).
import ghidra.app.emulator.EmulatorHelper;
import ghidra.pcode.emulate.BreakCallBack;
import ghidra.pcode.pcoderaw.PcodeOpRaw;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import java.util.*;

final class TcHw {
    static TcHw on;  // the function being prepared, or null
    final TreeMap<Long, Long> reads = new TreeMap<>();  // listed register -> the target's value
    final Map<Long, Integer> widths = new HashMap<>();  // listed register -> its width in bytes (4 unless narrower)
    final TreeSet<Long> writes = new TreeSet<>();  // listed registers stored to
    boolean gbr, bl, cache;

    // Script arg 11: "-", or "hw" then "_"-separated items: "r" + register + "." + value (8 hex digits each; 2 per
    // byte of a narrower register),
    // "w" + register, "gbr", "bl", "cache" (core/hw_ops.arg; no ",;= ": analyzeHeadless.bat splits arguments at them).
    static TcHw parse(String a) {
        if (a == null || !a.startsWith("hw")) return null;
        TcHw h = new TcHw();
        for (String it : a.substring(2).replaceFirst("^:", "").split("_")) {
            if (it.equals("gbr")) h.gbr = true;
            else if (it.equals("bl")) h.bl = true;
            else if (it.equals("cache")) h.cache = true;
            else if (it.startsWith("r") && it.contains(".")) {
                String[] p = it.substring(1).split("\\.");
                h.reads.put(Long.parseLong(p[0], 16), Long.parseLong(p[1], 16));
                h.widths.put(Long.parseLong(p[0], 16), p[1].length() / 2);
            } else if (it.startsWith("w")) h.writes.add(Long.parseLong(it.substring(1), 16));
            else if (!it.isEmpty()) throw new IllegalArgumentException("hw_ops arg: unknown item " + it);
        }
        return h;
    }

    static boolean gbrOp(Instruction in) {
        String m = in.getMnemonicString().toLowerCase().replace("_", "");
        if (!m.matches("(ldc|stc)(\\.l)?")) return false;
        String t = in.toString().toLowerCase().replace("_", "");
        String[] ops = t.substring(t.indexOf(' ') + 1).split(",");
        return (m.startsWith("ldc") ? ops[ops.length - 1] : ops[0]).trim().equals("gbr");
    }

    // meta.json "sh4_special" (core/agent.py: the helper for each): "ldc gbr", "stc gbr", "ocbi", ... for a listed op.
    String special(Instruction in) {
        String m = in.getMnemonicString().toLowerCase().replace("_", "");
        if (gbr && gbrOp(in)) return m.substring(0, 3) + " gbr";
        return cache && m.matches("ocbi|ocbp|ocbwb") ? m : null;
    }

    // null, or the first hardware op in the body the listing does not name: a TcCatalog.SYS instruction other than
    // the SR moves (TcSr) and the listed GBR moves and cache ops, or a hardware pool constant (outside the store
    // queues) used as an address that is not a listed register. The same ops TcCatalog counts as sys_ops and hw_refs.
    String check(TcBase o, AddressSetView body) {
        for (Instruction in : o.listing.getInstructions(body, true)) {
            String m = in.getMnemonicString().toLowerCase().replaceFirst("^_", "");
            String text = in.toString().toLowerCase();
            if (TcCatalog.SYS.contains(m) && !TcCatalog.isSrOp(m, text) && special(in) == null) {
                return "hardware op " + text + " at " + TcBase.hex(in.getAddress().getOffset()) + " is not listed";
            }
            Long pv = m.equals("mov.l") ? o.poolValue(in) : null;
            if (pv == null) continue;
            long v = TcBase.fold(pv);
            String[] ops = text.split(",");
            String dst = ops[ops.length - 1].trim();
            if (TcCatalog.isHw(v) && !TcCatalog.isSq(v) && dst.startsWith("r") && !reads.containsKey(v)
                && !writes.contains(v) && TcCatalog.usedAsAddress(o.listing, in, dst, body)) {
                return "hardware register " + TcBase.hex(v) + " at " + TcBase.hex(in.getAddress().getOffset())
                    + " is not listed";
            }
        }
        return null;
    }

    int width(long reg) {
        return widths.getOrDefault(reg, 4);
    }

    // TcFold: the bytes of a load inside one listed register (big endian), else null.
    byte[] read(long off, int size) {
        Map.Entry<Long, Long> e = reads.floorEntry(off);
        int w = e == null ? 0 : width(e.getKey());
        if (e == null || off + size > e.getKey() + w) return null;
        byte[] b = new byte[size];
        for (int i = 0; i < size; i++) b[i] = (byte) (e.getValue() >> (8 * (w - 1 - (off - e.getKey() + i))));
        return b;
    }

    // A register's value as the arg gave it: 8 hex digits, 2 per byte of a narrower register.
    String value(long reg) {
        int w = width(reg);
        return w == 4 ? TcBase.hex(reads.get(reg)) : String.format("%0" + (2 * w) + "x", reads.get(reg));
    }

    // TcExit.p4, TcLoads.storeOutside: a written range [lo, hi) inside one listed register (its 4 bytes).
    static boolean written(long lo, long hi) {
        Long r = on == null ? null : on.writes.floor(lo);
        return r != null && hi <= r + 4;
    }

    // TcOracle: listed cache ops do nothing (the SLEIGH's pcodeops; without a callback the emulator stops there).
    static void callbacks(EmulatorHelper emu) {
        if (on == null || !on.cache) return;
        for (String name : new String[] {"CacheBlockInvalidate", "CacheBlockPurge", "CacheBlockWriteBack"}) {
            emu.registerCallOtherCallback(name, new BreakCallBack() {
                @Override
                public boolean pcodeCallback(PcodeOpRaw op) {
                    return true;
                }
            });
        }
    }

    // meta.json "hw_ops" (core/agent.py tells the C how to write each listed op; core/compare judges the writes).
    // "writes" and "cache" only when listed: a listing of reads, gbr and bl alone keeps the meta it had before them.
    String meta() {
        StringBuilder s = new StringBuilder(), w = new StringBuilder();
        for (Map.Entry<Long, Long> e : reads.entrySet()) {
            if (s.length() > 0) s.append(", ");
            s.append('"').append(TcBase.hex(e.getKey())).append("\": \"").append(value(e.getKey())).append('"');
        }
        for (long r : writes) w.append(w.length() > 0 ? ", " : "").append('"').append(TcBase.hex(r)).append('"');
        return "{\"reads\": {" + s + "}" + (writes.isEmpty() ? "" : ", \"writes\": [" + w + "]") + ", \"gbr\": " + gbr
            + ", \"bl\": " + bl + (cache ? ", \"cache\": true" : "") + "}";
    }

    // vectors.txt header lines (before the first vector): Y per listed register (its width after the value when not
    // 4), Z for GBR state.
    static String header() {
        if (on == null) return "";
        StringBuilder s = new StringBuilder();
        for (Map.Entry<Long, Long> e : on.reads.entrySet()) {
            int w = on.width(e.getKey());
            s.append("Y ").append(TcBase.hex(e.getKey())).append(' ').append(TcBase.hex(e.getValue()))
                .append(w == 4 ? "" : " " + w).append('\n');
        }
        return s.append(on.gbr ? "Z\n" : "").toString();
    }

    // expected.txt lines IM (TcSr.exit) and GB (Result.gbr, -1 without the listing), each or "".
    static String exit(TcOracle.Result r) {
        return TcSr.exit(r.imask) + (r.gbr < 0 ? "" : "GB " + TcBase.hex(r.gbr) + "\n");
    }
}
