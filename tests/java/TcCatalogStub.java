// TcCatalog.usedAsAddress on a stub listing (tests/test_catalog_hw.py): the instructions come from a small table, no
// Ghidra application, no language. Only what usedAsAddress asks of an Instruction (getNext, getAddress, toString),
// of the listing (getInstructionAt, for a branch target) and of the body (contains) is there.
//
// usage: java TcCatalogStub CASES      CASES lines (hex addresses; # starts a comment; a blank line ends a case):
//   I ADDR TEXT          a 2-byte instruction as Ghidra prints it (a delay slot starts with "_")
//   U ADDR REG           prints "U ADDR REG: true|false", usedAsAddress from the instruction at ADDR for REG
// Each case's body is its own instructions.
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import java.lang.reflect.Proxy;
import java.nio.file.*;
import java.util.*;

public class TcCatalogStub {
    static final AddressSpace RAM = new GenericAddressSpace("ram", 32, AddressSpace.TYPE_RAM, 0);

    @SuppressWarnings("unchecked")
    static <T> T stub(Class<T> c, Map<String, java.util.function.Function<Object[], Object>> m) {
        return (T) Proxy.newProxyInstance(TcCatalogStub.class.getClassLoader(), new Class<?>[] {c}, (p, meth, args) -> {
            java.util.function.Function<Object[], Object> f = m.get(meth.getName());
            if (f != null) return f.apply(args);
            switch (meth.getName()) {
            case "hashCode": return System.identityHashCode(p);
            case "equals": return p == args[0];
            default: throw new UnsupportedOperationException(c.getSimpleName() + "." + meth.getName());
            }
        });
    }

    static void run(TreeMap<Long, String> ins, List<String[]> asks) {
        Map<Long, Instruction> made = new HashMap<>();
        AddressSetView body = stub(AddressSetView.class, Map.of(
            "contains", a -> a.length == 1 && a[0] instanceof Address && ins.containsKey(((Address) a[0]).getOffset())));
        java.util.function.Function<Long, Instruction>[] get = new java.util.function.Function[1];
        get[0] = addr -> made.computeIfAbsent(addr, k -> stub(Instruction.class, Map.of(
            "getAddress", a -> RAM.getAddress(k),
            "toString", a -> ins.get(k),
            "getNext", a -> {
                Long nx = ins.higherKey(k);
                return nx == null ? null : get[0].apply(nx);
            })));
        Listing listing = stub(Listing.class, Map.of("getInstructionAt", a -> {
            long k = ((Address) a[0]).getOffset();
            return ins.containsKey(k) ? get[0].apply(k) : null;
        }));
        for (String[] q : asks) {
            long at = Long.parseLong(q[0], 16);
            System.out.println("U " + q[0] + " " + q[1] + ": " + TcCatalog.usedAsAddress(listing, get[0].apply(at), q[1], body));
        }
    }

    public static void main(String[] args) throws Exception {
        TreeMap<Long, String> ins = new TreeMap<>();
        List<String[]> asks = new ArrayList<>();
        for (String raw : Files.readAllLines(Paths.get(args[0]))) {
            String line = raw.replaceAll("#.*", "").trim();
            if (line.isEmpty()) {
                if (!ins.isEmpty()) run(ins, asks);
                ins = new TreeMap<>();
                asks = new ArrayList<>();
                continue;
            }
            String[] f = line.split("\\s+", 3);
            if (f[0].equals("I")) ins.put(Long.parseLong(f[1], 16), f[2]);
            else if (f[0].equals("U")) asks.add(new String[] {f[1], f[2]});
        }
        if (!ins.isEmpty()) run(ins, asks);
    }
}
