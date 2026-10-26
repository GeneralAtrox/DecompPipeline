// Register dataflow over one SH-4 function, for prototype repair (TcSig). Not a script itself.
// Masks: bit i (0..15) is fr i, bit 16+k is r(4+k), the integer argument registers, bit 20+k is r k (r0..r3),
// bit 24+k is r(8+k) (r8..r14, callee-saved).
//
// A must-define pass over the basic blocks finds (1) registers that some path reads before writing, and
// (2) argument registers that reach a call unwritten: the caller's entry value passes straight through to the
// callee (`mov #1,r4; jsr` forwarding the caller's r5). Ghidra cannot see (2) when it does not know the callee
// takes that register, but the judge compares it at the call, so the C code needs it as a parameter.
// It also finds the registers a function leaves changed (written other than by restoring a saved value): the
// vector library keeps its results in fr12..fr15 (`fmov.s @r4+,fr14`), hand-written display-list helpers advance a
// pointer kept in r14; both are compared as runtime state. The changed mask also decides return registers (TcSig).
import ghidra.program.model.address.*;
import ghidra.program.model.block.*;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import ghidra.util.task.TaskMonitor;
import java.util.*;
import java.util.function.ToIntFunction;

public class TcFlow {
    static final int ALL = 0x7fffffff;
    static final int INTS = 0xf0000;
    static final int LOW = 0xf00000; // r0..r3: inputs only under a private convention (target.json abi extra_inputs)
    static final int ISTATE = 0x7f000000; // r8..r14: callee-saved, so never arguments; state when read at entry
    static final int CLOBBER = 0xfff | INTS | LOW; // a call clobbers fr0..fr11 and r0..r7 (the SH ABI's caller-saved)
    static final int STATE = 0xf000; // fr12..fr15: callee-saved, so never arguments; the vector library's state
    static final int RET = (1 << 20) | 1; // r0 and fr0: a mocked callee sets both (its scripted return value)

    final Program prog;
    final Listing listing;
    final TaskMonitor monitor;
    final Register[] fr = new Register[16];
    // Callee summaries (TcSum; target.json "param_summaries"): null keeps the SH ABI (a call defines and changes every
    // caller-saved register). calleeDefs: what a known callee writes on every path (r0 and fr0 always count, its
    // result); calleeWrites: what it may leave changed. passAbi: arguments reaching a call are judged under the ABI
    // model (D_ABI) instead of the oracle's mocks: the callee's real inputs (TcSum's reads). restoreIsWrite: a pop the
    // function did not push itself (an epilogue fragment restoring its caller's r8..r14) is a change.
    ToIntFunction<Function> calleeDefs, calleeWrites;
    boolean passAbi, restoreIsWrite;
    int exitDefs; // after entryFlow: registers written on every path to a return (rts or a tail call), not saved
    // After entryFlow with callee summaries: each address the code runs on into without a call or branch (SP 8c0587b0
    // `mov #0,r7` running into 8c0587b4), with the AND of the D_ABI masks there; TcSum treats each as a tail call.
    final Map<Address, Integer> fallOuts = new LinkedHashMap<>();

    TcFlow(Program prog, TaskMonitor monitor) {
        this.prog = prog;
        this.listing = prog.getListing();
        this.monitor = monitor;
        for (int i = 0; i < 16; i++) fr[i] = prog.getRegister("fr" + i);
    }

    // The mask of a parameter storage name: r0..r14, frN, or the pair drN (frN and frN+1); 0 for anything else.
    static int maskOf(String storage) {
        if (storage.matches("r[0-3]")) return 1 << (20 + Integer.parseInt(storage.substring(1)));
        if (storage.matches("r[4-7]")) return 1 << (12 + Integer.parseInt(storage.substring(1)));
        if (storage.matches("r([89]|1[0-4])")) return 1 << (16 + Integer.parseInt(storage.substring(1)));
        if (storage.matches("fr([0-9]|1[0-5])")) return 1 << Integer.parseInt(storage.substring(2));
        if (storage.matches("dr([02468]|1[024])")) return 3 << Integer.parseInt(storage.substring(2));
        return 0;
    }

    // The register a mask bit stands for.
    static String nameOf(int bit) {
        return bit < 16 ? "fr" + bit : bit < 20 ? "r" + (bit - 12) : bit < 24 ? "r" + (bit - 20) : "r" + (bit - 16);
    }

    // {float registers and r0..r3 that some path reads before writing, argument registers that reach a call unwritten,
    //  1 if f switches FPSCR modes or banks (fschg, frchg, fpchg): its float masks are then unsafe,
    //  r4..r7 and r8..r14 that some path reads before writing, fr12..fr15 written other than by restoring a saved
    //  value, every register written other than by a restore and not saved by the function (ints and floats)}.
    // calleeArgs gives the registers a callee's harness compares (a mask).
    int[] entryFlow(Function f, ToIntFunction<Function> calleeArgs) {
        AddressSetView body = TcTail.extent(prog, f); // the body and the shared tails f runs
        Map<Address, Address> cont = TcTail.prContinuations(prog, body, calleeDefs != null); // PR-redirected tail calls (TcTail)
        TcSaves sv = new TcSaves(listing, body); // what counts as a save and a restore (TcSaves)
        int modes = 0, restored = 0; // restored: registers some `mov.l @r15+,rN` puts back (isRestore)
        for (Instruction in : listing.getInstructions(body, true)) {
            if (in.getMnemonicString().toLowerCase().replace("_", "").matches("fschg|frchg|fpchg")) modes = 1;
            if (sv.isRestore(in)) {
                for (Object o : in.getResultObjects()) {
                    if (o instanceof Register) restored |= frMask((Register) o) | intMask((Register) o);
                }
            }
        }
        Set<Address> called = new HashSet<>();
        for (Function g : f.getCalledFunctions(monitor)) called.add(g.getEntryPoint());
        called.addAll(TcTail.tailCallees(prog, f, body));
        if (calleeDefs != null) called.addAll(TcTail.jumpCallees(prog, body)); // summaries: `bra` into g, a tail call
        Map<Address, Integer> argsAt = new HashMap<>(); // call site -> callee argument mask (-1: not a known call)
        List<CodeBlock> blocks = new ArrayList<>();
        try {
            CodeBlockIterator it = new BasicBlockModel(prog).getCodeBlocksContaining(body, monitor);
            while (it.hasNext()) blocks.add(it.next());
        } catch (Exception e) {
            throw new IllegalStateException("block model failed for " + f.getEntryPoint(), e);
        }
        Map<Address, Address> contBlock = new HashMap<>(); // continuation block -> the block of its redirected jump
        for (Map.Entry<Address, Address> e : cont.entrySet()) {
            Address at = e.getKey(); // with summaries: the block holding it when that block starts outside the extent
            for (CodeBlock b : blocks) {
                if (calleeDefs != null && b.contains(at) && !body.contains(b.getFirstStartAddress())) at = b.getFirstStartAddress();
            }
            for (CodeBlock b : blocks) if (b.contains(e.getValue())) contBlock.put(at, b.getFirstStartAddress());
        }
        // Four must-define masks per block end (bit set: written on every path there), indexed by D_*:
        // reads follow the SH ABI (a call leaves every caller-saved register changed); arguments reaching a call follow
        // the oracle (a mocked callee leaves every register as it was but r0 and fr0, its scripted return value), so a
        // register still holding the entry value at a later call is compared there with that value: the caller's own
        // input, passed on (Toy Commander 8c036434 hands its entry fr4 to 8c039c0e after two calls).
        // The *_DIRECT masks follow direct flow only (no computed jumps): r0..r3 count only when unwritten on such a
        // path, because Ghidra's guessed targets of a computed jump can be wrong (Toy Commander's memset 8c04eed8: its
        // `jmp @r1` into an unrolled store run also "reaches" a loop that reads r3 only after setting it). Real r0..r3
        // inputs are SHC helpers' and hand-written code's, without jump tables.
        Map<Address, int[]> out = new HashMap<>();
        int reads = 0, pass = 0, ireads = 0, writes = 0, saved = 0;
        boolean changed = true;
        while (changed) {
            changed = false;
            for (CodeBlock b : blocks) {
                Address start = b.getFirstStartAddress();
                int[] d = new int[4];
                if (!start.equals(f.getEntryPoint())) {
                    for (int k = 0; k < 4; k++) d[k] = definedOnEntry(b, body, out, k, contBlock.get(start));
                }
                int pending = -1; // a call's argument mask: read after its delay slot, then the call clobbers
                boolean clobbers = false;
                int defs = CLOBBER; // what the pending call defines (ABI: every caller-saved register)
                int passK = passAbi ? D_ABI : D_MOCK;
                for (Instruction in : code(b, body)) {
                    int[] vec = vectorOperands(in);
                    reads |= fresh(vec[0], d, D_ABI);
                    if (in.getDelaySlotDepth() > 0) {
                        // Ghidra merges the delay slot's operands into the branch's; the slot is visited next with
                        // its own, so the branch contributes only its target register (`jsr @r3`, `braf r0`).
                        reads |= fresh(branchReads(in) & LOW, d, D_ABI);
                        ireads |= branchReads(in) & (INTS | ISTATE) & ~d[D_ABI];
                    } else {
                        boolean save = sv.isSave(in), restore = sv.isRestore(in);
                        int zeroed = zeroes(in);
                        String kept = sv.contextStored(in); // a context save's stored register: not a read
                        for (Object o : in.getInputObjects()) {
                            if (!(o instanceof Register) || ((Register) o).getName().equalsIgnoreCase(kept)) continue;
                            int m = frMask((Register) o) | intMask((Register) o);
                            if (save) {
                                saved |= m;
                                // a callee-saved register, or one the code restores (a helper preserving r3 and r4
                                // around its work, Toy Commander's strcpy 8c067e88): not an input
                                if ((m & ~(STATE | ISTATE | restored)) == 0) continue;
                            }
                            m &= ~zeroed;
                            reads |= fresh(m & (0xffff | LOW), d, D_ABI);
                            ireads |= m & (INTS | ISTATE) & ~d[D_ABI];
                        }
                        for (Object o : in.getResultObjects()) {
                            if (!(o instanceof Register)) continue;
                            int m = frMask((Register) o) | intMask((Register) o);
                            define(d, m);
                            if (!restore || restoreIsWrite) writes |= m;
                        }
                    }
                    define(d, vec[1]);
                    writes |= vec[1];
                    if (pending >= 0) {
                        pass |= fresh(pending, d, passK);
                        if (clobbers) clobber(d, defs);
                        pending = -1;
                    }
                    int args = argsAt.computeIfAbsent(in.getAddress(), a -> callArgs(in, body, called, calleeArgs));
                    if (args >= 0 || in.getFlowType().isCall()) {
                        List<Function> gs = calleeDefs == null ? List.of() : calleesOf(in, body, called);
                        defs = gs.isEmpty() ? CLOBBER : over(gs, calleeDefs, true) | RET;
                        // the callee's results (r0, fr0, ...) may be the function's own
                        writes |= gs.isEmpty() || calleeWrites == null ? CLOBBER : over(gs, calleeWrites, false);
                        clobbers = in.getFlowType().isCall(); // a tail jump does not return; a bt/bf one falls through
                        if (in.getDelaySlotDepth() > 0) {
                            pending = Math.max(args, 0);
                        } else {
                            pass |= fresh(Math.max(args, 0), d, passK);
                            if (clobbers) clobber(d, defs);
                        }
                    }
                }
                if (pending >= 0) {
                    pass |= fresh(pending, d, passK);
                    if (clobbers) clobber(d, defs);
                }
                int[] old = out.put(start, d);
                changed |= old == null || !Arrays.equals(old, d);
            }
        }
        // r8..r14 the function saves are its locals: a read of one "before writing" lies on a path the data rules out
        // (Toy Commander 8c05b87c sets r10 under one table test and reads it under another). Runtime state (the
        // display-list helpers' r14) is never saved.
        ireads &= ~(saved & ISTATE);
        if (calleeDefs != null) exitDefs = exitDefs(blocks, out, body, called) & ~saved;
        return new int[] {reads & (0xffff | LOW), pass, modes, ireads, writes & ~saved & STATE, writes & ~saved};
    }

    // AND over the blocks that return (rts) or tail-call a known function (its own defs added) of the block-end
    // D_ABI mask; 0 when no such block is found. A computed jump out of the body counts as a return.
    int exitDefs(List<CodeBlock> blocks, Map<Address, int[]> out, AddressSetView body, Set<Address> called) {
        int all = ALL;
        boolean any = false;
        for (CodeBlock b : blocks) {
            int[] d = out.get(b.getFirstStartAddress());
            if (d == null) continue;
            for (Instruction in : code(b, body)) {
                if (in.getFlowType().isCall()) continue;
                Address fo = fallOut(in, body);
                if (fo != null) { // runs on into other code: a tail call to the function there (none known: no defs)
                    Function h = prog.getFunctionManager().getFunctionAt(fo);
                    all &= d[D_ABI] | (h == null ? 0 : calleeDefs.applyAsInt(h));
                    any = true;
                    fallOuts.merge(fo, d[D_ABI], (x, y) -> x & y);
                }
                List<Function> gs = calleesOf(in, body, called);
                boolean leaves = in.getFlowType().isTerminal()
                    || (in.getFlowType().isJump() && in.getFlowType().isComputed() && !in.getFlowType().isConditional());
                if (!gs.isEmpty()) {
                    all &= d[D_ABI] | over(gs, calleeDefs, true);
                    any = true;
                } else if (leaves) {
                    all &= d[D_ABI];
                    any = true;
                }
            }
        }
        return any ? all : 0;
    }

    // The address `in` runs on into outside the body without a call or branch (a fall-through leaving it), or null. A
    // delay slot's own fall-through is its branch's, so it is skipped.
    static Address fallOut(Instruction in, AddressSetView body) {
        if (in.isInDelaySlot() || in.getFlowType().isCall()) return null;
        Address ft = in.getFallThrough();
        return ft == null || body.contains(ft) ? null : ft;
    }

    static final int D_ABI = 0, D_ABI_DIRECT = 1, D_MOCK = 2, D_MOCK_DIRECT = 3;

    static void define(int[] d, int m) {
        for (int k = 0; k < 4; k++) d[k] |= m;
    }

    // After a call returns: every caller-saved register (the ABI; with callee summaries, what the callee writes on
    // every path), or only r0 and fr0 (the oracle's mocked callee).
    static void clobber(int[] d, int defs) {
        d[D_ABI] |= defs;
        d[D_ABI_DIRECT] |= defs;
        d[D_MOCK] |= RET;
        d[D_MOCK_DIRECT] |= RET;
    }

    // The registers of m not yet written under model k (D_ABI or D_MOCK): r0..r3 judged along direct flow only.
    static int fresh(int m, int[] d, int k) {
        return (m & ~LOW & ~d[k]) | (m & LOW & ~d[k + 1]);
    }

    // The callee's argument mask when `in` calls or tail-jumps to a known function; -1 otherwise.
    int callArgs(Instruction in, AddressSetView body, Set<Address> called, ToIntFunction<Function> calleeArgs) {
        List<Function> gs = calleesOf(in, body, called);
        return gs.isEmpty() ? -1 : over(gs, calleeArgs, false);
    }

    // The known functions `in` calls or tail-jumps to (none: empty). Without summaries only the first; with them every
    // target of a computed call Ghidra resolved (Toy Commander 8c061630 `jsr @r0`, seven matrix routines): reads and
    // writes are their OR, defs their AND (over).
    List<Function> calleesOf(Instruction in, AddressSetView body, Set<Address> called) {
        List<Function> out = new ArrayList<>();
        for (Reference r : in.getReferencesFrom()) {
            Address to = r.getToAddress();
            if (!r.getReferenceType().isFlow() || !called.contains(to)) continue;
            if (!r.getReferenceType().isCall() && body.contains(to)) continue; // a branch inside f (or its tails)
            Function g = prog.getFunctionManager().getFunctionAt(to);
            if (g != null && !out.contains(g)) out.add(g);
            if (!out.isEmpty() && calleeDefs == null) break;
        }
        return out;
    }

    static int over(List<Function> gs, ToIntFunction<Function> m, boolean and) {
        int x = and ? ALL : 0;
        for (Function g : gs) x = and ? x & m.applyAsInt(g) : x | m.applyAsInt(g);
        return x;
    }

    // A block's instructions; with summaries only those in the extent (a PR continuation can start inside a block of
    // another function, Toy Commander 8c061aa0 in 8c061a98's block: the code before it is not this function's).
    Iterable<Instruction> code(CodeBlock b, AddressSetView body) {
        return listing.getInstructions(calleeDefs == null ? b : body.intersect(b), true);
    }

    // AND of the predecessors' masks (not yet computed: all set); a block entered only from outside f: none.
    // contFrom: for a PR continuation (TcTail), the block whose redirected jump returns here: the mocked callee in
    // between leaves every register as it was but r0 and fr0 (its scripted return value), like the oracle's mocks.
    // k: the mask (D_*); a *_DIRECT one leaves computed-jump edges out (a block entered only through them: all set).
    int definedOnEntry(CodeBlock b, AddressSetView body, Map<Address, int[]> out, int k, Address contFrom) {
        boolean direct = k == D_ABI_DIRECT || k == D_MOCK_DIRECT;
        int def = ALL;
        boolean any = false;
        try {
            CodeBlockReferenceIterator src = b.getSources(monitor);
            while (src.hasNext()) {
                CodeBlockReference ref = src.next();
                CodeBlock pb = ref.getSourceBlock();
                // a block of the extent's code, also one starting outside it: Toy Commander 8c061e54's block from its
                // entry runs into 8c061eac's continuation 8c061e60, and a branch from 8c061ec4's continuation splits it
                // at 8c061e80 (batch 13); the code after the split is reached from the extent's part of that block
                if (pb == null || !pb.intersects(body)) continue;
                if (direct && ref.getFlowType().isComputed()) {
                    any = true; // reached, but not along direct flow: contributes nothing
                    continue;
                }
                any = true;
                int[] o = out.get(pb.getFirstStartAddress());
                def &= o == null ? ALL : o[k];
            }
        } catch (Exception e) {
            throw new IllegalStateException("block sources failed at " + b.getFirstStartAddress(), e);
        }
        if (contFrom != null) {
            any = true;
            int[] o = out.get(contFrom);
            def &= (o == null ? ALL : o[k]) | RET;
        }
        return any ? def : 0;
    }

    // fipr/ftrv reach their vectors through computed register addresses in the patched SLEIGH, and fsca writes a
    // pair of singles (drN): their float registers come from the operand text. Returns {read mask, write mask}.
    static final java.util.regex.Pattern VEC_RE = java.util.regex.Pattern.compile("^(fipr|ftrv|fsca)\\s+(\\w+),(\\w+)$");

    static int[] vectorOperands(Instruction in) {
        java.util.regex.Matcher m = VEC_RE.matcher(in.toString().toLowerCase().replaceFirst("^_", ""));
        if (!m.matches()) return new int[] {0, 0};
        int n = Integer.parseInt(m.group(3).replaceAll("\\D", ""));
        if (m.group(1).equals("fsca")) return new int[] {0, 3 << n};
        if (m.group(1).equals("ftrv")) return new int[] {0xf << n, 0xf << n};
        return new int[] {(0xf << n) | (0xf << Integer.parseInt(m.group(2).replaceAll("\\D", ""))), 1 << (n + 3)};
    }

    // The patched SLEIGH gives every float instruction both FPSCR paths, so operand lists also name the 8-byte
    // pair (drN/xdN) of the PR=1/SZ=1 path. The game runs PR=0, SZ=0 throughout, so those are never real.
    int frMask(Register r) {
        if (r.getMinimumByteSize() == 8) return 0;
        long lo = r.getAddress().getOffset(), hi = lo + r.getMinimumByteSize();
        int m = 0;
        for (int i = 0; i < 16; i++) {
            if (fr[i] == null || !fr[i].getAddress().getAddressSpace().equals(r.getAddress().getAddressSpace())) continue;
            long a = fr[i].getAddress().getOffset();
            if (a < hi && a + fr[i].getMinimumByteSize() > lo) m |= 1 << i;
        }
        return m;
    }

    static int intMask(Register r) {
        return maskOf(r.getName().toLowerCase()) & (INTS | LOW | ISTATE);
    }

    static final java.util.regex.Pattern BRANCH_REG_RE = java.util.regex.Pattern.compile("^(?:jsr|jmp)\\s+@r(\\d+)$|^(?:braf|bsrf)\\s+r(\\d+)$");

    // The register a delayed branch itself reads: its target register (a mask; 0 for r15).
    static int branchReads(Instruction in) {
        java.util.regex.Matcher m = BRANCH_REG_RE.matcher(in.toString().toLowerCase().replaceFirst("^_", ""));
        if (!m.matches()) return 0;
        return maskOf("r" + (m.group(1) != null ? m.group(1) : m.group(2)));
    }

    static final java.util.regex.Pattern ZERO_RE = java.util.regex.Pattern.compile("^(?:xor|sub|subc)\\s+(r\\d+),\\1$");

    // An idiom whose result does not depend on its register's value (`xor r0,r0` and `sub r3,r3` give 0, `subc r3,r3`
    // gives -T): Ghidra lists the register as an input, but the code does not read it. Returns its mask, else 0.
    static int zeroes(Instruction in) {
        java.util.regex.Matcher m = ZERO_RE.matcher(in.toString().toLowerCase().replaceFirst("^_", ""));
        return m.matches() ? maskOf(m.group(1)) : 0;
    }
}
