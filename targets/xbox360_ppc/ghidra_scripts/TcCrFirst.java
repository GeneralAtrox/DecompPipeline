// The CR fields one instruction reads and sets, for TcFlow.entryReads (the CR fields TcOracle seeds per vector). Not a
// script itself.
//
// Counting every CR field a p-code op takes as an input made a record-form write a read: the SLEIGH sets cr0 bit by bit
// (ppc_common.sinc cr0flags -> setCrBit: `crReg = crReg & mask; crReg = crReg | tmp`), so `rlwinm. r10,r7,2,0,29` read
// cr0 first and Too Human 82a800d8, whose only CR read is the bc on that result, was seeded a random cr0 it never
// reads (its vectors changed: 40 of 117 then hit the step limit). So the instruction's p-code is run here on values
// that know which of a field's four bits still hold its old value: AND with a constant clears them, OR with something
// not drawn from the field keeps them, a multiply by a constant 0 or 1 (mtcrf's `cr0 * (CRM0 == 0)`) drops or keeps
// them; constants fold. Any other use of a value holding old bits (getCrBit's shift for a branch, mfcr's zext, mcrf
// copying it to another field) is a read of that field. A field written with no old bit left is set; one that keeps
// old bits (crxor on one bit, mtcrf's unselected fields) is neither set nor read. An instruction whose p-code branches
// inside itself (stwcx.'s `if (RESERVE == 0) goto inst_next`, a conditional bclr) keeps the rule before: every CR
// input a read, every CR output a write.
import ghidra.program.model.pcode.*;
import java.util.*;

public class TcCrFirst {
    static final class Effect {
        final Set<String> reads = new TreeSet<>(), defs = new TreeSet<>();
    }

    // A value in one instruction's p-code: a constant (k), or (field) the field's value with `old` its bits (of 4) that
    // still hold the field's value before the instruction, or neither (nothing of the CR's old value).
    static final class Val {
        final Long k;
        final String field;
        final int old;

        Val(Long k, String field, int old) {
            this.k = k;
            this.field = field;
            this.old = old;
        }
    }

    static final Val FRESH = new Val(null, null, 0);

    // The field a varnode is exactly (fields: register offset -> name, each `size` bytes), else null.
    static String exact(TreeMap<Long, String> fields, int size, Varnode v) {
        return v != null && v.isRegister() && v.getSize() == size ? fields.get(v.getOffset()) : null;
    }

    // The fields a register varnode overlaps.
    static List<String> overlap(TreeMap<Long, String> fields, int size, Varnode v) {
        List<String> out = new ArrayList<>();
        if (v == null || !v.isRegister()) return out;
        long lo = v.getOffset(), hi = lo + v.getSize();
        for (Map.Entry<Long, String> e : fields.entrySet()) {
            if (e.getKey() < hi && e.getKey() + size > lo) out.add(e.getValue());
        }
        return out;
    }

    static long mask(int bytes) {
        return bytes >= 8 ? -1L : (1L << 8 * bytes) - 1;
    }

    static Long fold(int code, long a, long b, int outSize) {
        long r;
        switch (code) {
            case PcodeOp.COPY: case PcodeOp.INT_ZEXT: r = a; break;
            case PcodeOp.INT_NEGATE: r = ~a; break;
            case PcodeOp.INT_AND: r = a & b; break;
            case PcodeOp.INT_OR: r = a | b; break;
            case PcodeOp.INT_XOR: r = a ^ b; break;
            case PcodeOp.INT_ADD: r = a + b; break;
            case PcodeOp.INT_SUB: r = a - b; break;
            case PcodeOp.INT_MULT: r = a * b; break;
            case PcodeOp.INT_LEFT: r = b >= 64 ? 0 : a << b; break;
            case PcodeOp.INT_RIGHT: r = b >= 64 ? 0 : a >>> b; break;
            case PcodeOp.INT_EQUAL: r = a == b ? 1 : 0; break;
            case PcodeOp.INT_NOTEQUAL: r = a != b ? 1 : 0; break;
            case PcodeOp.BOOL_NEGATE: r = a == 0 ? 1 : 0; break;
            default: return null;
        }
        return r & mask(outSize);
    }

    static void consume(Val x, Effect e) {
        if (x.field != null && x.old != 0) e.reads.add(x.field);
    }

    static Effect effect(List<PcodeOp> ops, TreeMap<Long, String> fields, int size) {
        Effect e = new Effect();
        boolean inner = false;
        for (int i = 0; i + 1 < ops.size(); i++) {
            int c = ops.get(i).getOpcode();
            inner |= c == PcodeOp.BRANCH || c == PcodeOp.CBRANCH || c == PcodeOp.BRANCHIND;
        }
        if (inner) {
            for (PcodeOp op : ops) {
                for (Varnode in : op.getInputs()) for (String f : overlap(fields, size, in)) if (!e.defs.contains(f)) e.reads.add(f);
                e.defs.addAll(overlap(fields, size, op.getOutput()));
            }
            return e;
        }
        Map<Varnode, Val> tmp = new HashMap<>();
        Map<String, Val> cur = new HashMap<>();  // the fields this instruction has written so far
        for (PcodeOp op : ops) {
            Val[] in = new Val[op.getNumInputs()];
            for (int i = 0; i < in.length; i++) {
                Varnode v = op.getInput(i);
                String f = exact(fields, size, v);
                if (v.isConstant()) {
                    in[i] = new Val(v.getOffset() & mask(v.getSize()), null, 0);
                } else if (f != null) {
                    in[i] = cur.getOrDefault(f, new Val(null, f, 15));
                } else {
                    for (String g : overlap(fields, size, v)) consume(cur.getOrDefault(g, new Val(null, g, 15)), e);
                    in[i] = tmp.getOrDefault(v, FRESH);
                }
            }
            Varnode o = op.getOutput();
            Val out = apply(op.getOpcode(), in, o == null ? 8 : o.getSize(), e);
            if (o == null) continue;
            String f = exact(fields, size, o);
            List<String> hit = overlap(fields, size, o);
            if (f != null) {
                if (out.field != null && !out.field.equals(f)) {   // mcrf: another field's value
                    consume(out, e);
                    out = FRESH;
                }
                cur.put(f, out.field == null ? new Val(null, f, 0) : out);
            } else if (!hit.isEmpty()) {
                consume(out, e);
                for (String g : hit) cur.put(g, new Val(null, g, 0));
            } else {
                if (!o.isUnique()) {   // old bits leaving for a register or memory: read
                    consume(out, e);
                    if (out.field != null) out = FRESH;
                }
                tmp.put(o, out);
            }
        }
        for (Map.Entry<String, Val> w : cur.entrySet()) if (w.getValue().old == 0) e.defs.add(w.getKey());
        return e;
    }

    static Val apply(int code, Val[] in, int outSize, Effect e) {
        if (in.length >= 1 && in.length <= 2 && in[0].k != null && (in.length == 1 || in[1].k != null)) {
            Long r = fold(code, in[0].k, in.length == 2 ? in[1].k : 0, outSize);
            if (r != null) return new Val(r, null, 0);
        }
        if (code == PcodeOp.COPY) return in[0];
        if (in.length == 2) {
            for (int s = 0; s < 2; s++) {
                Val x = in[s], y = in[1 - s];
                if (x.field == null) continue;
                if (code == PcodeOp.INT_AND && y.k != null) return new Val(null, x.field, x.old & (int) (y.k & 15));
                if (code == PcodeOp.INT_MULT && y.k != null && y.k == 0) return new Val(0L, null, 0);
                if (code == PcodeOp.INT_MULT && y.k != null && y.k == 1) return x;
                if (code == PcodeOp.INT_OR && y.field == null) return x;
                if (code == PcodeOp.INT_OR && y.field.equals(x.field)) return new Val(null, x.field, x.old | y.old);
            }
        }
        for (Val x : in) consume(x, e);
        return FRESH;
    }
}
