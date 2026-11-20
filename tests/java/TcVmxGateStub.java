// The per-op gate (tests/test_x360_vmx_gate.py): TcVmxConfirmed.refusal, with no Ghidra imports.
// usage: java TcVmxGateStub   prints "ops a,b,c" (the checked-in list, sorted), then for vaddfp, vrefp and lvx:
// "named OP" (refused by an empty set, naming the op), "admitted OP" (a set holding it) and "other_op_still_refused OP".
import java.util.*;

public class TcVmxGateStub {
    public static void main(String[] a) {
        System.out.println("ops " + String.join(",", new TreeSet<>(TcVmxConfirmed.OPS)));
        for (String op : new String[] {"vaddfp", "vrefp", "lvx"}) {
            String r = TcVmxConfirmed.refusal(op, Set.of());
            System.out.println((("VMX unconfirmed " + op).equals(r) ? "named " : "WRONG named ") + op);
            System.out.println(TcVmxConfirmed.refusal(op, Set.of(op)) == null ? "admitted " + op : "WRONG admitted " + op);
            System.out.println(TcVmxConfirmed.refusal(op, Set.of("vxor")) != null ? "other_op_still_refused " + op
                : "WRONG other_op_still_refused " + op);
        }
    }
}
