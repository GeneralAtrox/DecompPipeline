// tests/test_strict_judge.py's view of TcJunk and TcStrict without Ghidra.
//
// usage: java TcStrictStub KEYS FACTS...   KEYS: lines "seed vid call slot k" (decimal, seed and vid as signed
//                                          longs); prints TcJunk.at (hex), at8, fr (hex) per line. Then per facts
//                                          file (TcFacts.load): its strict bits, TcStrict.header and TcStrict.meta,
//                                          each on one line with newlines as "|". Then, where TcStrict has the
//                                          callee masks (Dreamcast, Saturn), with two of them set by hand (TcStrict
//                                          .callees needs Ghidra): 8c020000 changing all scratch registers,
//                                          8c030000 keeping r6 and fr8: the last facts file's header, and changes()
//                                          of 8c030000 and of an unlisted target, in hex. Last, there, the CALL
//                                          record field of r1..r7 and fr1..fr11 (TcStrict.recordField), and of r0
//                                          and fr12 (-1: junk never reaches them).
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;

public class TcStrictStub {
    public static void main(String[] args) throws Exception {
        StringBuilder out = new StringBuilder();
        for (String line : Files.readAllLines(Path.of(args[0]))) {
            String[] t = line.trim().split("\\s+");
            long seed = Long.parseLong(t[0]), vid = Long.parseLong(t[1]), call = Long.parseLong(t[2]);
            long slot = Long.parseLong(t[3]);
            out.append(Long.toHexString(TcJunk.at(seed, vid, call, slot))).append(' ')
                .append(TcJunk.at8(seed, vid, call, slot, Integer.parseInt(t[4]))).append(' ')
                .append(Long.toHexString(TcJunk.fr(TcJunk.at(seed, vid, call, slot)))).append('\n');
        }
        for (int i = 1; i < args.length; i++) {
            TcFacts f = TcFacts.load(args[i]);
            out.append(f.strict).append('\n').append(TcStrict.header(f).replace('\n', '|')).append('\n')
                .append(TcStrict.meta(f).replace('\n', '|')).append('\n');
        }
        Field changes;
        try {
            changes = TcStrict.class.getDeclaredField("changes");
        } catch (NoSuchFieldException e) {
            changes = null;  // the GameCube's TcStrict: no callee masks
        }
        if (changes != null && args.length > 1) {
            int all = TcStrict.class.getDeclaredField("SCRATCH").getInt(null);
            @SuppressWarnings("unchecked")
            Map<Long, Integer> m = (Map<Long, Integer>) changes.get(null);
            m.put(0x8c020000L, all);
            m.put(0x8c030000L, all & ~(1 << 18) & ~(1 << 8));  // r6, fr8 (SH TcFlow.maskOf; GC has none)
            Method of = TcStrict.class.getDeclaredMethod("changes", long.class);
            out.append(TcStrict.header(TcFacts.load(args[args.length - 1])).replace('\n', '|')).append('\n')
                .append(Integer.toHexString((int) of.invoke(null, 0x8c030000L))).append(' ')
                .append(Integer.toHexString((int) of.invoke(null, 0x8c099990L))).append('\n');
            m.clear();
        }
        try {  // TcStrict.recordField (Dreamcast, Saturn) of each register junk can reach: compare.py REG_INDEX
            Method rf = TcStrict.class.getDeclaredMethod("recordField", String.class);
            for (int i = 1; i <= 11; i++) {
                for (String r : i <= 7 ? new String[] {"r" + i, "fr" + i} : new String[] {"fr" + i}) {
                    out.append(r).append('=').append(rf.invoke(null, r)).append(' ');
                }
            }
            out.append(rf.invoke(null, "r0")).append(' ').append(rf.invoke(null, "fr12")).append('\n');
        } catch (NoSuchMethodException e) {
            // the GameCube's TcStrict: its fields are its own (r4..r10, f2..f8: leftover)
        }
        System.out.print(out);
    }
}
