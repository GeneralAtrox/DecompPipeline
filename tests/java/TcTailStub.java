// TcTail.extent on a stub program (tests/test_tail.py): the instructions come from a small table, no Ghidra
// application, no language. Only what TcTail and TcExit.halts ask of a Program is there.
//
// usage: java TcTailStub CASES      CASES lines (hex addresses; # starts a comment):
//   F ENTRY START END          a function, its body START..END (bytes, inclusive)
//   I ADDR KIND TEXT [-> T]    a 2-byte instruction; KIND fall, call, jump, cjump, term or slot (a delay slot);
//                              T: its flow target (a call's callee, a branch's target)
//   X ENTRY                    prints "X ENTRY: RANGES", TcTail.extent of that function
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import java.lang.reflect.Proxy;
import java.nio.file.*;
import java.util.*;

public class TcTailStub {
    static final AddressSpace RAM = new GenericAddressSpace("ram", 32, AddressSpace.TYPE_RAM, 0);
    static final TreeMap<Long, String[]> INS = new TreeMap<>();  // addr -> {kind, text, target or null}
    static final Map<Long, long[]> FNS = new HashMap<>();         // entry -> {start, end}
    static Program prog;

    interface Fn {
        Object call(Object[] a);
    }

    static Address at(long a) {
        return RAM.getAddress(a);
    }

    @SuppressWarnings("unchecked")
    static <T> T stub(Class<T> c, Map<String, Fn> m) {
        return (T) Proxy.newProxyInstance(TcTailStub.class.getClassLoader(), new Class<?>[] {c}, (p, meth, args) -> {
            Fn f = m.get(meth.getName());
            if (f != null) return f.call(args);
            switch (meth.getName()) {
            case "hashCode": return System.identityHashCode(p);
            case "equals": return p == args[0];
            case "toString": return c.getSimpleName();
            default: throw new UnsupportedOperationException(c.getSimpleName() + "." + meth.getName());
            }
        });
    }

    static <T> Object iter(Class<?> c, List<T> items) {
        Iterator<T> it = items.iterator();
        Map<String, Fn> m = new HashMap<>();
        m.put("hasNext", a -> it.hasNext());
        m.put("next", a -> it.next());
        Object[] self = new Object[1];
        m.put("iterator", a -> self[0]);
        self[0] = stub(c, m);
        return self[0];
    }

    static FlowType flow(String kind) {
        switch (kind) {
        case "call": return RefType.UNCONDITIONAL_CALL;
        case "jump": return RefType.UNCONDITIONAL_JUMP;
        case "cjump": return RefType.CONDITIONAL_JUMP;
        case "term": return RefType.TERMINATOR;
        default: return RefType.FALL_THROUGH;
        }
    }

    static boolean slot(long a) {
        String[] s = INS.get(a);
        return s != null && s[0].equals("slot");
    }

    static Reference ref(long from, long to, RefType t) {
        Map<String, Fn> m = new HashMap<>();
        m.put("getFromAddress", a -> at(from));
        m.put("getToAddress", a -> at(to));
        m.put("getReferenceType", a -> t);
        return stub(Reference.class, m);
    }

    static Instruction ins(long a) {
        String[] s = INS.get(a);
        if (s == null) return null;
        FlowType ft = flow(s[0]);
        boolean branch = !s[0].equals("fall") && !s[0].equals("slot");
        int depth = branch && slot(a + 2) ? 1 : 0;
        Map<String, Fn> m = new HashMap<>();
        m.put("getProgram", x -> prog);
        m.put("getAddress", x -> at(a));
        m.put("getMinAddress", x -> at(a));
        m.put("getMaxAddress", x -> at(a + 1));
        m.put("isInDelaySlot", x -> s[0].equals("slot"));
        m.put("getFlowType", x -> ft);
        m.put("getDelaySlotDepth", x -> depth);
        m.put("getNext", x -> ins(a + 2));
        m.put("getPrevious", x -> ins(a - 2));
        m.put("getMnemonicString", x -> s[1].split("\\s+")[0]);
        m.put("toString", x -> s[1]);
        m.put("getFallThrough", x -> s[0].equals("slot") || !ft.hasFallthrough() ? null : at(a + 2 + 2 * depth));
        m.put("getFlows", x -> s[2] == null ? new Address[0] : new Address[] {at(Long.parseLong(s[2], 16))});
        m.put("getReferencesFrom", x -> s[2] == null ? new Reference[0]
            : new Reference[] {ref(a, Long.parseLong(s[2], 16), ft)});
        return stub(Instruction.class, m);
    }

    static Function fn(long entry) {
        long[] b = FNS.get(entry);
        if (b == null) return null;
        Map<String, Fn> m = new HashMap<>();
        m.put("getEntryPoint", x -> at(entry));
        m.put("getBody", x -> new AddressSet(at(b[0]), at(b[1])));
        return stub(Function.class, m);
    }

    static Program program() {
        Map<String, Fn> lm = new HashMap<>();
        lm.put("getInstructionAt", x -> ins(((Address) x[0]).getOffset()));
        lm.put("getInstructions", x -> {
            List<Instruction> out = new ArrayList<>();
            for (long a : INS.keySet()) {
                if (x[0] instanceof AddressSetView v ? v.contains(at(a)) : a >= ((Address) x[0]).getOffset()) {
                    out.add(ins(a));
                }
            }
            return iter(InstructionIterator.class, out);
        });
        Listing listing = stub(Listing.class, lm);
        Map<String, Fn> fm = new HashMap<>();
        fm.put("getFunctionAt", x -> fn(((Address) x[0]).getOffset()));
        FunctionManager functions = stub(FunctionManager.class, fm);
        Map<String, Fn> rm = new HashMap<>();
        rm.put("getReferencesTo", x -> {
            List<Reference> out = new ArrayList<>();
            for (Map.Entry<Long, String[]> e : INS.entrySet()) {
                String t = e.getValue()[2];
                if (t != null && Long.parseLong(t, 16) == ((Address) x[0]).getOffset()) {
                    out.add(ref(e.getKey(), Long.parseLong(t, 16), flow(e.getValue()[0])));
                }
            }
            return iter(ReferenceIterator.class, out);
        });
        ReferenceManager refs = stub(ReferenceManager.class, rm);
        Map<String, Fn> pm = new HashMap<>();
        pm.put("getListing", x -> listing);
        pm.put("getFunctionManager", x -> functions);
        pm.put("getReferenceManager", x -> refs);
        return stub(Program.class, pm);
    }

    public static void main(String[] args) throws Exception {
        prog = program();
        for (String line : Files.readAllLines(Paths.get(args[0]))) {
            String l = line.replaceFirst("#.*", "").trim();
            if (l.isEmpty()) continue;
            String[] w = l.split("\\s+", 3);
            long a = Long.parseLong(w[1], 16);
            if (w[0].equals("F")) {
                String[] r = w[2].split("\\s+");
                FNS.put(a, new long[] {Long.parseLong(r[0], 16), Long.parseLong(r[1], 16)});
            } else if (w[0].equals("I")) {
                String[] k = w[2].split("\\s+", 2);
                String[] t = k[1].split("\\s*->\\s*");
                INS.put(a, new String[] {k[0], t[0].trim(), t.length > 1 ? t[1].trim() : null});
            } else if (w[0].equals("X")) {
                StringBuilder out = new StringBuilder("X " + w[1] + ":");
                for (AddressRange r : TcTail.extent(prog, fn(a))) {
                    out.append(String.format(" %x-%x", r.getMinAddress().getOffset(), r.getMaxAddress().getOffset()));
                }
                System.out.println(out);
            }
        }
    }
}
