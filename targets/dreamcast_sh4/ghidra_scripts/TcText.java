// The function's own text in a vector (TcOracle.setEntry). Not a script itself.
//
// The bytes of the instructions a function runs (TcTail.extent) and of the literal-pool words it loads
// (TcBase.poolValue: `mov.l`/`mov.w @(disp,PC)`) are program text, not inputs: no C version reads them (the C runner
// faults on a read of an instruction byte, rule R8, and a pool word is a constant in the C). A patch over them, from a
// hint or a generator, makes the oracle run a program other than the original. Each such byte gets the image's byte
// back, in the vector itself, so its runs and its M lines agree. Toy Commander's hint for 8c061ec4 once set the pool
// word 8c061ed4 (its PR constant, the return-address trick) to the oracle's sentinel, so the runs returned to the
// caller and skipped the continuation at 8c061ee4 (batch 13). TcPrepare reports "text_restored": the vectors changed.
import ghidra.program.model.address.*;
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
        return out;
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
