// Float-guided arguments (docs/PROOF_GATES.md, float arguments). TcInputs draws f32 parameters with niceFloat: 0, +-1,
// 0.5, integers and spreads, so a value next to a pool constant the code compares against almost never comes up. TC
// 8c023148 sets a rectangle's translucent flag with `fcmp/gt fr8,fr3` against the pool's 0.999; C written with
// `1.0f > a` differs only for a in [0.999, 1.0) and passed every vector.
// The locked side harvests the f32 constants an fcmp tests (a pool word loaded through `mova` + `fmov.s @rN` or
// `@rN+`, the pointer moved by `mov` or `add #k`, the constant copied with `fmov`), and in about a third of the
// vectors (every vector of a rescue pass) puts one of them, or its neighbour one ulp down or up, in each f32
// parameter (fr4..fr11, or r4..r7 typed f32) with even odds: TcFret's counterpart for arguments. Its generator is its
// own, seeded from the vector's (seed, id, try), so TcInputs' and TcGuide's sequences are unchanged, and a function
// with no such constant or no f32 parameter keeps its vectors. Unlike TcGuide it does not wait for
// TcFacts.coverageInputs. The listing is read in address order, a constant kept for HELD instructions while its
// register is not written (as TcGuide does). Not a script itself; used by TcVectors.
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSetView;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.Memory;
import java.util.*;
import java.util.regex.*;

final class TcFguide {
    static final Pattern LOAD = Pattern.compile("^fmov\\.s\\s+@(r\\d+)(\\+?),(fr\\d+)$");
    static final Pattern COPY = Pattern.compile("^fmov\\s+(fr\\d+),(fr\\d+)$");
    static final Pattern MOV = Pattern.compile("^mov\\s+(r\\d+),(r\\d+)$");
    static final Pattern ADD = Pattern.compile("^add\\s+#(-?(?:0x)?[0-9a-f]+),(r\\d+)$");
    static final Pattern FCMP = Pattern.compile("^fcmp/(?:gt|eq)\\s+(fr\\d+),(fr\\d+)$");
    static final int HELD = 64; // instructions a loaded constant stays tracked (not written) before its compare

    private static Address memoAt;
    private static List<Long> memo = List.of();

    private TcFguide() {
    }

    // The f32 constants (bits) f's fcmp instructions test, each with its neighbours one ulp down and up, in code order.
    static List<Long> constants(Listing listing, AddressSetView body, Memory mem) {
        if (body.getMinAddress().equals(memoAt)) return memo;
        LinkedHashSet<Long> out = new LinkedHashSet<>();
        Map<String, Long> ptr = new HashMap<>();  // rN -> the pool address it holds
        Map<String, long[]> fr = new HashMap<>(); // frN -> {constant bits, instructions left}
        for (Instruction in : listing.getInstructions(body, true)) {
            String t = in.toString().toLowerCase().replaceFirst("^_", "");
            fr.values().removeIf(c -> --c[1] < 0);
            Matcher m;
            if ((m = FCMP.matcher(t)).matches()) {
                for (int g = 1; g <= 2; g++) if (fr.containsKey(m.group(g))) add(out, fr.get(m.group(g))[0]);
                continue;
            }
            Long pool = mova(in), moved = null;
            String fdst = null, rdst = null;
            long[] val = null;
            if (pool != null) {
                moved = pool;
                rdst = "r0";
            } else if ((m = LOAD.matcher(t)).matches() && ptr.containsKey(m.group(1))) {
                long a = ptr.get(m.group(1));
                Long w = word(mem, body.getMinAddress().getNewAddress(a));
                if (w != null && ((w >> 23) & 0xff) != 0xff) val = new long[] {w, HELD};
                fdst = m.group(3);
                if (!m.group(2).isEmpty()) {
                    moved = a + 4;
                    rdst = m.group(1);
                }
            } else if ((m = COPY.matcher(t)).matches()) {
                fdst = m.group(2);
                val = fr.containsKey(m.group(1)) ? fr.get(m.group(1)).clone() : null;
            } else if ((m = MOV.matcher(t)).matches() && ptr.containsKey(m.group(1))) {
                moved = ptr.get(m.group(1));
                rdst = m.group(2);
            } else if ((m = ADD.matcher(t)).matches() && ptr.containsKey(m.group(2))) {
                moved = ptr.get(m.group(2)) + Long.decode(m.group(1));
                rdst = m.group(2);
            }
            for (Object o : in.getResultObjects()) {
                if (!(o instanceof Register)) continue;
                String r = ((Register) o).getName().toLowerCase();
                fr.remove(r);
                ptr.remove(r);
            }
            if (moved != null) ptr.put(rdst, moved);
            if (val != null) fr.put(fdst, val);
        }
        memoAt = body.getMinAddress();
        memo = new ArrayList<>(out);
        return memo;
    }

    // The target of `mova @(disp,pc),r0` (encoding 0xc7dd: (pc & ~3) + 4 + disp * 4), else null.
    static Long mova(Instruction in) {
        try {
            byte[] b = in.getBytes();
            if (b.length != 2 || (b[0] & 0xff) != 0xc7) return null;  // big endian: the opcode byte first
            return ((in.getAddress().getOffset() & ~3L) + 4 + (b[1] & 0xff) * 4L) & 0xffffffffL;
        } catch (Exception e) {
            return null;
        }
    }

    static Long word(Memory mem, Address a) {
        try {
            return mem.getInt(a) & 0xffffffffL;
        } catch (Exception e) {
            return null;
        }
    }

    static void add(Set<Long> out, long bits) {
        float f = Float.intBitsToFloat((int) bits);
        out.add(bits & 0xffffffffL);
        out.add(Float.floatToRawIntBits(Math.nextDown(f)) & 0xffffffffL);
        out.add(Float.floatToRawIntBits(Math.nextUp(f)) & 0xffffffffL);
    }

    // Guide one vector v (TcInputs' draw, before the function's hints): about one in three, or always (rescue).
    static void guide(TcInputs.Vec v, TcBase.Proto proto, Listing listing, AddressSetView body, Memory mem, long seed,
                      int id, int idTry, boolean always) {
        boolean any = false;
        for (TcBase.Param q : proto.params) any |= q.kind.equals("f32");
        List<Long> consts = any ? constants(listing, body, mem) : List.of();
        Random h = new Random(seed * 0x9e3779b97f4a7c15L + id * 0xbf58476d1ce4e5b9L + idTry * 0x94d049bb133111ebL);
        if (consts.isEmpty() || (!always && h.nextInt(3) != 0)) return;
        for (TcBase.Param q : proto.params) {
            if (!q.kind.equals("f32") || !h.nextBoolean()) continue;
            long c = consts.get(h.nextInt(consts.size()));
            if (q.storage.matches("fr([4-9]|1[01])")) v.fr[Integer.parseInt(q.storage.substring(2)) - 4] = (int) c;
            else if (q.storage.matches("r[4-7]")) v.r[q.storage.charAt(1) - '4'] = c;
        }
    }
}
