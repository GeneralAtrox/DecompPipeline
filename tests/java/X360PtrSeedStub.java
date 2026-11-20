// tests/test_x360_ptrseed.py: the Xbox 360 oracle's pointer seeding (TcPtrSeed.pick, repairRet, repair, the retN hint
// location of TcInputs.genPlace, TcBank.badWrite), compiled against targets/xbox360_ppc and the Ghidra jars, with no Ghidra application
// and no program. Prints "WRONG ..." per failed check and "ALL OK" when none failed.
import ghidra.pcode.memstate.MemoryFaultHandler;
import ghidra.pcode.memstate.MemoryPageBank;
import ghidra.program.model.address.*;
import java.util.*;

public class X360PtrSeedStub {
    static int wrong = 0;

    static void check(String what, Object got, Object want) {
        if (!Objects.equals(got, want)) {
            wrong++;
            System.out.println("WRONG " + what + ": got " + got + ", want " + want);
        }
    }

    static long[] ld(long pc, long addr, long size, long val) {
        return new long[] {pc, addr, size, val};
    }

    public static void main(String[] args) {
        // 0. a branch into data is the input's fault, not the body's (TcRun.intoData; Too Human 82e40388's blr to 4000079c)
        check("into scratch", TcRun.intoData(0x4000079cL), true);
        check("into stack", TcRun.intoData(0x700fdff8L), true);
        check("into null", TcRun.intoData(0), true);
        check("high word ignored", TcRun.intoData(0xffffffff4000079cL), true);
        check("image code", TcRun.intoData(0x82e40400L), false);
        check("import thunk", TcRun.intoData(0x8312ec1cL), false);
        long[] rets = new long[16];
        rets[0] = 2;            // a small result: near a fault at 0x20
        rets[1] = 0x40000400L;  // ours (scratch): never repaired
        rets[2] = 0;            // null
        List<Integer> seen3 = Arrays.asList(0, 1, 2);
        Set<Long> none = new HashSet<>();
        List<long[]> loads = new ArrayList<>();
        loads.add(ld(0x82000000L, 0x40001100L, 4, 0x0c));   // load 0, before call 1
        loads.add(ld(0x82000004L, 0x40001104L, 4, 0x7fffffffL)); // load 1, far from the fault
        // 1. the newest call near the fault wins when no near load came after it
        check("newest call", TcPtrSeed.pick(0x20, rets, seen3, loads, 2, none, 0x4000, true), 2);
        // 2. a near load after the call: repairVec goes first (newest), then the call (fallback)
        List<long[]> after = new ArrayList<>(loads);
        after.add(ld(0x82000008L, 0x40001108L, 4, 0x10));    // load 2, after call 2 (2 loads seen before it)
        check("load after call", TcPtrSeed.pick(0x20, rets, seen3, after, 3, none, 0x4000, true), -1);
        check("fallback", TcPtrSeed.pick(0x20, rets, seen3, after, 3, none, 0x4000, false), 2);
        // 3. a load repairVec already set does not count; nor a byte load
        Set<Long> poked = new HashSet<>(Collections.singletonList(0x40001108L));
        check("poked load", TcPtrSeed.pick(0x20, rets, seen3, after, 3, poked, 0x4000, true), 2);
        List<long[]> bytes = new ArrayList<>(loads);
        bytes.add(ld(0x82000008L, 0x40001108L, 1, 0x10));
        check("byte load", TcPtrSeed.pick(0x20, rets, seen3, bytes, 3, none, 0x4000, true), 2);
        // 4. the log holds only the last loads: indices are global (seen), so a logged load older than the call loses
        check("rolled log", TcPtrSeed.pick(0x20, rets, Arrays.asList(0, 1, 70), loads, 70, none, 0x4000, true), 2);
        // 5. ours, far, and not yet made are skipped. Two calls made: call 1 is ours, and load 0 came after call 0
        check("only made", TcPtrSeed.pick(0x20, rets, Arrays.asList(0, 1), loads, 2, none, 0x4000, true), -1);
        check("only made, fallback", TcPtrSeed.pick(0x20, rets, Arrays.asList(0, 1), loads, 2, none, 0x4000, false), 0);
        check("far", TcPtrSeed.pick(0x9000, rets, seen3, loads, 2, none, 0x4000, true), -1);
        check("ours", TcPtrSeed.pick(0x40000420L, rets, Arrays.asList(0, 0), new ArrayList<>(), 0, none, 0x4000, true), -1);
        long[] up = new long[16];
        up[0] = 0x1234567800000004L;  // a 64-bit register: its low word is the pointer
        check("low word", TcPtrSeed.pick(0x24, up, Arrays.asList(0), new ArrayList<>(), 0, none, 0x4000, true), 0);
        check("17 calls", TcPtrSeed.pick(0x20, new long[16], Collections.nCopies(17, 0), new ArrayList<>(), 0, none, 0x4000, true), 15);

        // repairRet: the picked result and every later one not ours become fresh chunks of the heap
        TcPrepare g = new TcPrepare();
        g.rnd = new Random(1);
        TcInputs.Vec v = new TcInputs.Vec();
        v.rets = rets.clone();
        TcOracle.Result r = new TcOracle.Result();
        r.faultAddr = 0x20;
        r.callSeen.addAll(seen3);
        r.seen = 2;
        check("repaired", TcPtrSeed.repairRet(g, v, r, loads, true), true);
        check("call 0 kept", v.rets[0], 2L);
        check("call 1 kept", v.rets[1], 0x40000400L);
        for (int j = 2; j < 16; j++) check("call " + j + " ours", TcPtrSeed.owned(v.rets[j]) && v.rets[j] >= g.GEN_HEAP_LO, true);
        check("chunk patch", v.patchAddr.size(), 14);
        check("again, newest: none", TcPtrSeed.repairRet(g, v, r, loads, true), false);   // call 0 is older than load 0

        // genPlace: retN is set like a register, its +steps dereference it
        TcInputs.Vec w = new TcInputs.Vec();
        g.rnd = new Random(2);
        g.applyHints(w, Arrays.asList("genptr ret3+0x20", "genrange ret3+0x2c 0x7fff0000 0x7fff0000", "genret 4 7",
                                      "genrange ret5 9 9", "genptr r4+0x8"));
        check("ret3 ours", TcPtrSeed.owned(w.rets[3]) && w.rets[3] >= g.GEN_HEAP_LO, true);
        check("ret3+0x20 is a chunk", TcPtrSeed.owned(w.genPoked.getOrDefault(w.rets[3] + 0x20, 0L)), true);
        check("ret3+0x2c", w.genPoked.get(w.rets[3] + 0x2c), 0x7fff0000L);
        check("genret", w.rets[4], 7L);
        check("bare ret5", w.rets[5], 9L);
        check("r4 still works", TcPtrSeed.owned(w.r[1]) && w.genPoked.containsKey(w.r[1] + 8), true);
        try {
            g.applyHints(new TcInputs.Vec(), Arrays.asList("genptr ret16+0x4"));
            check("ret16 refused", "accepted", "refused");
        } catch (RuntimeException e) {
            check("ret16 refused", "refused", "refused");
        }
        order(g);
        badWrite();
        System.out.println(wrong == 0 ? "ALL OK" : wrong + " WRONG");
    }
    // repair: the newest call goes before repairVec, and repairVec before an older call
    static void order(TcPrepare g) {
        g.rnd = new Random(3);
        long[] rets = new long[16];
        rets[0] = 4;
        List<long[]> loads = new ArrayList<>();
        loads.add(ld(0x82000000L, 0x83000000L, 4, 8));              // load 0, after call 0: repairVec's
        TcInputs.Vec v = new TcInputs.Vec();
        v.rets = rets.clone();
        Arrays.fill(v.r, 0x40000400L);
        TcOracle.Result r = new TcOracle.Result();
        r.faultAddr = 0x14;
        r.callSeen.add(0);
        r.seen = 1;
        check("repairVec first", TcPtrSeed.repair(g, v, r, loads) == v && TcPtrSeed.owned(v.genPoked.get(0x83000000L)), true);
        check("call 0 not yet", v.rets[0], 4L);
        check("then the call", TcPtrSeed.repair(g, v, r, loads) == v && TcPtrSeed.owned(v.rets[0]), true);
        check("then none", TcPtrSeed.repair(g, v, r, loads), null);
        r.callSeen.set(0, 1);                                        // the call came after the load: it goes first
        TcInputs.Vec w = new TcInputs.Vec();
        w.rets = rets.clone();
        check("newest call first", TcPtrSeed.repair(g, w, r, loads) == w && TcPtrSeed.owned(w.rets[0]) && w.genPoked.isEmpty(), true);
    }

    // TcBank.badWrite: the first tracked store below the image outside the regions, so the run stops at it
    static void badWrite() {
        AddressSpace ram = new GenericAddressSpace("ram", 32, AddressSpace.TYPE_RAM, 3);
        MemoryFaultHandler zero = new MemoryFaultHandler() {
            @Override
            public boolean uninitializedRead(Address address, int size, byte[] buf, int bufOffset) {
                for (int i = 0; i < size; i++) buf[bufOffset + i] = 0;
                return true;
            }

            @Override
            public boolean unknownAddress(Address address, boolean write) {
                return false;
            }
        };
        TcBank b = new TcBank(new MemoryPageBank(ram, true, 4096, zero), TcFacts.defaults());
        b.setChunk(0x54, 4, new byte[4]);
        check("untracked store", b.badWrite, -1L);
        b.track = true;
        b.setChunk(TcBase.HEAP_LO + 0x10, 4, new byte[4]);
        b.setChunk(0x83000000L, 4, new byte[4]);
        check("heap and image stores", b.badWrite, -1L);
        b.setChunk(0x54, 4, new byte[4]);
        b.setChunk(0x60, 4, new byte[4]);
        check("first bad store", b.badWrite, 0x54L);
    }
}
