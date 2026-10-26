// The control flow of the body for the reach pass (TcReach): its basic blocks under TcPrepare's indices
// (TcVectors.blockIndex), their successors inside the body, each block's branch instruction, and the frontier toward a
// block (kept apart from TcReach to keep files short). LOCKED: the watcher agent may not change this file. Not a script.
import ghidra.program.model.block.*;
import ghidra.program.model.listing.*;
import ghidra.util.task.TaskMonitor;
import java.util.*;

final class TcCfg {
    final TreeMap<Long, Integer> blockIndex;
    final Map<Integer, List<Integer>> succ = new TreeMap<>();
    final Map<Integer, Long> branchPc = new TreeMap<>();  // block -> its last jump (not a call)
    final Map<Integer, List<Integer>> cmpRegs = new TreeMap<>();  // block -> r0..r15 read by the compare before it

    // The function's constant data: from its first instruction to the end of the last literal-pool word or mova target
    // (a braf jump table) it addresses. Constant in the program, so never an input (TC 8c0144da: a mutated jump-table
    // entry sent the braf anywhere, and a matched C "fixed" to follow it).
    long constLo = Long.MAX_VALUE, constHi = Long.MIN_VALUE;

    boolean isConst(long a) {
        return a >= constLo && a < constHi;
    }

    TcCfg(TcVectors g) throws Exception {
        blockIndex = g.blockIndex;
        for (Instruction in : g.listing.getInstructions(g.body, true)) {
            long a = in.getAddress().getOffset();
            constLo = Math.min(constLo, a);
            constHi = Math.max(constHi, a + in.getLength());
            boolean pc = in.getMnemonicString().toLowerCase().replace("_", "").equals("mova") || g.poolValue(in) != null;
            for (ghidra.program.model.symbol.Reference r : in.getReferencesFrom()) {
                long to = r.getToAddress().getOffset();
                if (pc && r.getToAddress().isMemoryAddress() && Math.abs(to - a) <= 1024) {
                    constLo = Math.min(constLo, to);
                    constHi = Math.max(constHi, to + 4);
                }
            }
        }
        BasicBlockModel bbm = new BasicBlockModel(g.getCurrentProgram());
        CodeBlockIterator it = bbm.getCodeBlocksContaining(g.body, TaskMonitor.DUMMY);
        while (it.hasNext()) {
            CodeBlock cb = it.next();
            Integer i = blockIndex.get(cb.getMinAddress().getOffset());
            if (i == null) continue;
            List<Integer> out = new ArrayList<>();
            CodeBlockReferenceIterator d = cb.getDestinations(TaskMonitor.DUMMY);
            while (d.hasNext()) {
                Integer j = blockIndex.get(d.next().getDestinationAddress().getOffset());
                if (j != null && !out.contains(j)) out.add(j);
            }
            Collections.sort(out);
            succ.put(i, out);
            List<Integer> cmp = List.of();
            for (Instruction in : g.listing.getInstructions(cb, true)) {
                String m = in.getMnemonicString().toLowerCase().replace("_", "");
                if (m.startsWith("cmp") || m.startsWith("tst") || m.equals("dt")) {
                    cmp = new ArrayList<>();
                    for (Object o : in.getInputObjects()) {
                        String r = o instanceof ghidra.program.model.lang.Register ? o.toString().toLowerCase() : "";
                        if (r.matches("r([0-9]|1[0-5])") && !cmp.contains(Integer.parseInt(r.substring(1)))) {
                            cmp.add(Integer.parseInt(r.substring(1)));
                        }
                    }
                }
                if (in.getFlowType().isJump() && !in.getFlowType().isCall()) {
                    branchPc.put(i, in.getAddress().getOffset());
                    cmpRegs.put(i, cmp);
                }
            }
        }
    }

    Integer block(long addr) {
        Map.Entry<Long, Integer> e = blockIndex.floorEntry(addr);
        return e == null ? null : e.getValue();
    }

    // Can the control flow get from block a to block b?
    boolean reaches(int a, int b) {
        Deque<Integer> todo = new ArrayDeque<>(List.of(a));
        Set<Integer> seen = new HashSet<>(todo);
        while (!todo.isEmpty()) {
            int x = todo.pop();
            if (x == b) return true;
            for (int y : succ.getOrDefault(x, List.of())) if (seen.add(y)) todo.push(y);
        }
        return false;
    }

    // Frontier edges toward block tb: {covered block with a branch, its uncovered successor that can reach tb}.
    List<int[]> frontier(Set<Integer> covered, Integer tb) {
        List<int[]> out = new ArrayList<>();
        if (tb == null) return out;
        for (int b : covered) {
            if (!branchPc.containsKey(b)) continue;
            for (int s : succ.getOrDefault(b, List.of())) {
                if (!covered.contains(s) && reaches(s, tb)) out.add(new int[] {b, s});
            }
        }
        return out;
    }
}
