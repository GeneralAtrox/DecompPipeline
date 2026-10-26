// The function's own text in a vector (TcOracle.setEntry). Not a script itself.
//
// The bytes of the instructions a function runs (TcTail.extent) and of the literal-pool words it loads
// (TcBase.poolValue: `mov.l`/`mov.w @(disp,PC)`) are program text, not inputs: no C version reads them (the C runner
// faults on a read of an instruction byte, rule R8, and a pool word is a constant in the C). A patch over them, from a
// hint or a generator, makes the oracle run a program other than the original. Each such byte gets the image's byte
// back, in the vector itself, so its runs and its M lines agree. Toy Commander's hint for 8c061ec4 once set the pool
// word 8c061ed4 (its PR constant, the return-address trick) to the oracle's sentinel, so the runs returned to the
// caller and skipped the continuation at 8c061ee4 (batch 13). TcPrepare reports "text_restored": the vectors changed.
// The pool words a body reads through a `mova` base are text too (movaWords): Spawn 8c0565b0 loads its callees with
// mov.l @(0x14..0x20,r14) after `mova 8c0565f0,r0; mov r0,r14`, and a repair once set 8c056610 (Xmtrx_Pop's address)
// to 0x8cc01500, so the original called into scratch and no C naming its callees could match (hidden vector 97).
import ghidra.program.model.address.*;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.symbol.*;
import java.util.*;
import java.util.function.Function;

public class TcText {
    // The text of `code`: its instruction bytes and the pool words its PC-relative loads read.
    static Set<Long> of(Listing listing, AddressSetView code, Function<Instruction, Long> poolValue) {
        Set<Long> out = new HashSet<>();
        for (Instruction in : listing.getInstructions(code, true)) {
            for (long a = 0; a < in.getLength(); a++) out.add(in.getAddress().getOffset() + a);
            if (poolValue.apply(in) == null) continue;
            int n = in.getMnemonicString().toLowerCase().replace("_", "").equals("mov.l") ? 4 : 2;
            for (Reference r : in.getReferencesFrom()) {
                if (!r.getReferenceType().isRead() || !r.getToAddress().isMemoryAddress()) continue;
                for (long a = 0; a < n; a++) out.add(r.getToAddress().getOffset() + a);
            }
        }
        List<Long> at = new ArrayList<>();
        List<Integer> op = new ArrayList<>();
        List<Boolean> w0 = new ArrayList<>();
        for (Instruction in : listing.getInstructions(code, true)) {
            byte[] b;
            try {
                b = in.getBytes();
            } catch (MemoryAccessException e) {
                continue;
            }
            if (b.length != 2) continue;
            boolean r0 = false;
            for (Object o : in.getResultObjects()) r0 |= o instanceof Register g && g.getName().equalsIgnoreCase("r0");
            at.add(in.getAddress().getOffset());
            op.add((b[0] & 0xff) | (b[1] & 0xff) << 8);
            w0.add(r0);
        }
        out.addAll(movaWords(at, op, w0));
        return out;
    }

    // The bytes the body reads through a `mova @(disp,PC),r0` base. r0 holds it on the straight line after the mova,
    // until an instruction writes r0 or a branch (after its delay slot) ends the line. A register that a `mov r0,Rn`
    // there copies it into holds it everywhere in the body: Spawn 8c0565b0's paths each load through r14, and one
    // path's delay slot reloads r14 before another path's loads, so a straight-line rule would miss two of its three
    // callee words. A load through that register where it holds something else reads at most 60 bytes past the base,
    // which is the image's constant data or code either way. at/op/writesR0: the body's 2-byte instructions in
    // address order, their opcodes, and whether each writes r0.
    static Set<Long> movaWords(List<Long> at, List<Integer> op, List<Boolean> writesR0) {
        Set<Long> out = new HashSet<>();
        Map<Integer, Set<Long>> base = new HashMap<>();
        for (int i = 0; i < at.size(); i++) {
            if ((op.get(i) & 0xff00) != 0xc700) continue;
            long t = (at.get(i) & ~3L) + 4 + (op.get(i) & 0xff) * 4L;
            for (int j = i + 1; j < at.size() && j <= i + 32 && at.get(j) == at.get(j - 1) + 2; j++) {
                int o = op.get(j);
                read(o, 0, t, out);
                if ((o & 0xf0ff) == 0x6003 && (o & 0x0f00) != 0) {  // mov r0,Rn
                    base.computeIfAbsent(o >> 8 & 0xf, k -> new HashSet<>()).add(t);
                }
                if (writesR0.get(j) || branch(op.get(j - 1)) == 2 || branch(o) == 1) break;
            }
        }
        for (int i = 0; i < at.size(); i++) {
            for (Map.Entry<Integer, Set<Long>> e : base.entrySet()) {
                for (long t : e.getValue()) read(op.get(i), e.getKey(), t, out);
            }
        }
        return out;
    }

    // 2: a delayed branch (bra bsr braf bsrf jmp jsr rts rte bt/s bf/s); 1: bt or bf; 0: no branch.
    static int branch(int o) {
        int hi = o >>> 12, lo = o & 0xff;
        if (hi == 0xa || hi == 0xb || (o & 0xff00) == 0x8d00 || (o & 0xff00) == 0x8f00) return 2;
        if (o == 0x000b || o == 0x002b || (hi == 0 && (lo == 0x23 || lo == 0x03))) return 2;
        if (hi == 4 && (lo == 0x2b || lo == 0x0b)) return 2;
        return (o & 0xff00) == 0x8900 || (o & 0xff00) == 0x8b00 ? 1 : 0;
    }

    // Adds the bytes opcode `o` loads through register m when m holds t: mov.l @(disp,Rm),Rn; mov.b / mov.w
    // @(disp,Rm),R0; mov.b / mov.w / mov.l @Rm and @Rm+; fmov.s @Rm and @Rm+.
    static void read(int o, int m, long t, Set<Long> out) {
        int hi = o >>> 12, rm = o >> 4 & 0xf, lo = o & 0xf;
        long off;
        int n;
        if (hi == 5 && rm == m) {
            off = lo * 4L;
            n = 4;
        } else if ((o & 0xff00) == 0x8400 && rm == m) {
            off = lo;
            n = 1;
        } else if ((o & 0xff00) == 0x8500 && rm == m) {
            off = lo * 2L;
            n = 2;
        } else if (hi == 6 && rm == m && lo <= 6 && lo != 3) {
            off = 0;
            n = 1 << (lo % 4);
        } else if (hi == 0xf && rm == m && (lo == 8 || lo == 9)) {
            off = 0;
            n = 4;
        } else {
            return;
        }
        for (long a = 0; a < n; a++) out.add(t + off + a);
    }

    // Puts the image's bytes back where a patch covers text; true when a byte changed.
    static boolean restore(Memory mem, Set<Long> text, List<Long> addr, List<byte[]> data) {
        boolean changed = false;
        AddressSpace space = mem.getProgram().getAddressFactory().getDefaultAddressSpace();
        for (int i = 0; i < addr.size(); i++) {
            byte[] d = data.get(i);
            for (int j = 0; j < d.length; j++) {
                long a = TcBase.fold(addr.get(i) + j);
                if (!text.contains(a)) continue;
                byte b;
                try {
                    b = mem.getByte(space.getAddress(a));
                } catch (MemoryAccessException e) {
                    continue; // not initialized: no instruction or pool word is there
                }
                changed |= d[j] != b;
                d[j] = b;
            }
        }
        return changed;
    }
}
