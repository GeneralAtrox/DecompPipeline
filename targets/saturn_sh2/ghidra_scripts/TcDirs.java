// Branch-direction coverage (target.json "branch_coverage"; TcFacts.branchCoverage). Every conditional branch of the
// body (an instruction whose flow is conditional: a conditional jump, return or call) has two directions: fallthrough
// (index 2i) and taken (2i+1), the branches numbered in address order. TcOracle records the direction a branch took
// from the pc its step leads to: a delayed branch's slot runs in the same step, so the fallthrough is the address after
// the slot. With a fixed target, the pc is the target (taken) or the fallthrough; a branch whose target is its own
// fallthrough counts both. With a computed target (a conditional return), any pc but the fallthrough is taken.
// Report only: oracle_summary.json's branch_dirs_* and the verdict's coverage grade (core/proof.py); no vector,
// expected output or judge reads it. Not a script itself; the same file in every target.
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSetView;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.Listing;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

final class TcDirs {
    final long[] at, fall, target;  // per branch: its address, its fallthrough, its fixed target (-1: computed)
    final Map<Long, Integer> index = new HashMap<>();

    // rows: {address, fallthrough, target or -1}, in address order
    TcDirs(List<long[]> rows) {
        at = new long[rows.size()];
        fall = new long[rows.size()];
        target = new long[rows.size()];
        for (int i = 0; i < rows.size(); i++) {
            at[i] = rows.get(i)[0];
            fall[i] = rows.get(i)[1];
            target[i] = rows.get(i)[2];
            index.put(at[i], i);
        }
    }

    static TcDirs of(Listing listing, AddressSetView body) {
        List<long[]> rows = new ArrayList<>();
        for (Instruction in : listing.getInstructions(body, true)) {
            if (!in.getFlowType().isConditional()) continue;
            Address f = in.getAddress().add(in.getLength());
            for (int d = 0; d < in.getDelaySlotDepth(); d++) {  // the slot runs in the branch's step
                Instruction slot = listing.getInstructionAt(f);
                if (slot == null) break;
                f = f.add(slot.getLength());
            }
            Address[] flows = in.getFlows();
            rows.add(new long[] {in.getAddress().getOffset(), f.getOffset(),
                flows.length == 1 ? flows[0].getOffset() : -1});
        }
        return new TcDirs(rows);
    }

    int total() {
        return 2 * at.length;
    }

    // Before the step at pc: the direction the branch at prev (the address last stepped; -1 after a mocked call)
    // took, into dirs.
    void hit(Set<Integer> dirs, long prev, long pc) {
        Integer i = prev < 0 ? null : index.get(prev);
        if (i == null) return;
        if (target[i] < 0) {
            dirs.add(pc == fall[i] ? 2 * i : 2 * i + 1);
            return;
        }
        if (pc == fall[i]) dirs.add(2 * i);
        if (pc == target[i]) dirs.add(2 * i + 1);
    }

    // oracle_summary.json's fields (each followed by a comma): the directions, those the dev set and all valid vectors
    // hit, and the unhit ones as "address:t" (taken) or "address:f" (fallthrough), up to 64.
    String summary(Set<Integer> dev, Set<Integer> all) {
        StringBuilder unhit = new StringBuilder();
        int n = 0;
        for (int d = 0; d < total() && n < 64; d++) {
            if (all.contains(d)) continue;
            if (n++ > 0) unhit.append(',');
            unhit.append('"').append(String.format("%08x", at[d / 2])).append(d % 2 == 1 ? ":t" : ":f").append('"');
        }
        return String.format("\"branch_dirs_total\":%d,\"branch_dirs_hit_dev\":%d,\"branch_dirs_hit_all\":%d,%s",
            total(), dev.size(), all.size(), n > 0 ? "\"branch_dirs_unhit\":[" + unhit + "]," : "");
    }
}
