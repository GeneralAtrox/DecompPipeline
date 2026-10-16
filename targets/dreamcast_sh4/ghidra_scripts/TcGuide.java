// Constant-guided inputs (target.json "coverage_inputs"; TcFacts.coverageInputs): random inputs rarely hit the exact
// values a function compares against (`cmp/eq #3,r0`, a literal-pool constant, a switch bound), so the branches and
// calls behind those tests go unreached and a C version is never tested there. The locked side harvests those
// constants from the function's code and, in about a third of the vectors, puts them (and their neighbours c-1, c+1)
// where compared values usually come from: non-pointer integer parameters, scripted call results, words of the
// scratch window and the globals the function reads, at a byte, halfword or word position by the constant's size.
// TcInputs (the project's generator) is unchanged and never told which vectors were guided; its random sequence is
// consumed exactly as before (the guide has its own generator, seeded like TcVectors.stateValues), and dev and
// hidden vectors are guided alike. TcVectors' rescue pass guides every vector (always). A loaded constant counts when a
// compare uses its register within LOOKAHEAD instructions, or later while the register still holds it (a type code
// loaded once and tested after two calls). Not a script itself; used by TcVectors.
import ghidra.program.model.address.AddressSetView;
import ghidra.program.model.listing.*;
import java.util.*;
import java.util.function.Function;
import java.util.regex.*;

public class TcGuide {
    static final Pattern IMM_CMP = Pattern.compile("^(?:cmp/eq|tst|and|or|xor)\\s+#(-?(?:0x)?[0-9a-f]+),r0$");
    static final Pattern MOV_IMM = Pattern.compile("^mov\\s+#(-?(?:0x)?[0-9a-f]+),(r\\d+)$");
    static final Pattern POOL = Pattern.compile("^mov\\.[wl]\\s+\\S+,(r\\d+)$");
    static final Pattern CMP = Pattern.compile("^(?:cmp/(?:eq|hs|ge|hi|gt)|tst)\\s+(r\\d+),(r\\d+)$");
    static final int LOOKAHEAD = 6; // instructions between a constant's load and its compare
    static final int HELD = 64;     // further on, while the register still holds the constant (not written)

    // The constants f compares against (with their neighbours), deduplicated, in code order.
    static List<Long> constants(Listing listing, AddressSetView body, Function<Instruction, Long> pool) {
        LinkedHashSet<Long> out = new LinkedHashSet<>();
        List<Instruction> ins = new ArrayList<>();
        for (Instruction in : listing.getInstructions(body, true)) ins.add(in);
        for (int i = 0; i < ins.size(); i++) {
            String t = ins.get(i).toString().toLowerCase().replaceFirst("^_", "");
            Matcher m = IMM_CMP.matcher(t);
            if (m.matches()) {
                add(out, Long.decode(m.group(1)));
                continue;
            }
            Long c = null;
            String reg = null;
            if ((m = MOV_IMM.matcher(t)).matches()) {
                c = Long.decode(m.group(1));
                reg = m.group(2);
            } else if ((m = POOL.matcher(t)).matches() && (c = pool.apply(ins.get(i))) != null) {
                reg = m.group(1);
            }
            if (c == null) continue;
            for (int k = i + 1; k < Math.min(ins.size(), i + 1 + LOOKAHEAD + HELD); k++) {
                Matcher cm = CMP.matcher(ins.get(k).toString().toLowerCase().replaceFirst("^_", ""));
                if (cm.matches() && (cm.group(1).equals(reg) || cm.group(2).equals(reg))) {
                    add(out, c);
                    break;
                }
                if (k >= i + LOOKAHEAD && writes(ins.get(k), reg)) break;
            }
        }
        return new ArrayList<>(out);
    }

    static boolean writes(Instruction in, String reg) {
        for (Object o : in.getResultObjects()) {
            if (o instanceof ghidra.program.model.lang.Register
                && ((ghidra.program.model.lang.Register) o).getName().equalsIgnoreCase(reg)) return true;
        }
        return false;
    }

    static void add(Set<Long> out, long c) {
        for (long d = -1; d <= 1; d++) out.add((c + d) & 0xffffffffL);
    }

    // Guide one vector (about one in three, or always): v as TcInputs made it, before the function's hints are applied.
    static void guide(TcInputs.Vec v, TcBase.Proto proto, List<Long> consts, List<Long> globals, Random h,
                      long scrLo, boolean always) {
        if (consts.isEmpty() || (!always && h.nextInt(3) != 0)) return;
        for (int i = 0; i < proto.params.size(); i++) {
            String st = proto.params.get(i).storage;
            if (!st.matches("r[4-7]") || proto.paramIsPointer.get(i) || proto.params.get(i).kind.equals("f32")) continue;
            if (h.nextBoolean()) v.r[st.charAt(1) - '4'] = pick(consts, h);
        }
        for (int i = 0; i < v.rets.length; i++) if (h.nextBoolean()) v.rets[i] = pick(consts, h);
        for (int i = 0; i < v.patchAddr.size(); i++) {
            byte[] d = v.patchData.get(i);
            long a = v.patchAddr.get(i);
            if (a == scrLo) { // the scratch window: about one word in six
                for (int w = 0; w + 4 <= d.length; w += 4) if (h.nextInt(6) == 0) put(d, w, pick(consts, h), h);
            }
        }
        for (long g : globals) { // a global's own address, if TcInputs patched the block holding it
            for (int i = 0; i < v.patchAddr.size(); i++) {
                long a = v.patchAddr.get(i);
                byte[] d = v.patchData.get(i);
                if (a == scrLo || g < a || g + 4 > a + d.length || !h.nextBoolean()) continue;
                long c = pick(consts, h);
                int off = (int) (g - a);
                for (int k = 0; k < size(c) && off + k < d.length; k++) d[off + k] = (byte) (c >> (8 * k));
            }
        }
    }

    static long pick(List<Long> consts, Random h) {
        return consts.get(h.nextInt(consts.size()));
    }

    // Bytes a constant needs (as a byte, a halfword, or a word, signed or unsigned).
    static int size(long c) {
        long s = (int) c;
        if (s >= -128 && s <= 255) return 1;
        if (s >= -32768 && s <= 65535) return 2;
        return 4;
    }

    // c into the word at w, at a position its size allows (aligned), the other bytes kept.
    static void put(byte[] d, int w, long c, Random h) {
        int n = size(c), pos = n == 4 ? 0 : n == 2 ? 2 * h.nextInt(2) : h.nextInt(4);
        for (int k = 0; k < n; k++) d[w + pos + k] = (byte) (c >> (8 * k));
    }
}
