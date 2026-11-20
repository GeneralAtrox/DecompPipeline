// The GameCube template TcInputs.repairVec's sum fallback on fixed cases (tests/test_gc_refused_harness.py). No Ghidra
// application and no program: a bare TcPrepare holds the generator. Each case prints "NAME: null" (no repair: the
// vector is dropped, or TcVectors.repair tries the scripted call results) or "NAME: <what changed>".
import java.util.*;

public class TcGcRepairStub {
    static TcInputs.Vec vec() {
        TcInputs.Vec v = new TcInputs.Vec();
        for (int i = 0; i < 8; i++) v.r[i] = TcBase.SCR;  // our own pointers: never an index, never repaired
        v.patchAddr.add(0x81001000L);                      // a chunk this vector set
        v.patchData.add(new byte[0x400]);
        v.genHeapNext = 0x81001400L;                       // it is ours: the next chunk goes after it
        return v;
    }

    // The chunk's word at addr holds val (big endian), as a vector's input.
    static void put(TcInputs.Vec v, long addr, long val) {
        byte[] b = v.patchData.get(0);
        int o = (int) (addr - v.patchAddr.get(0));
        for (int k = 0; k < 4; k++) b[o + k] = (byte) (val >> (24 - 8 * k));
    }

    static void run(TcPrepare g, String name, TcInputs.Vec v, long fault, boolean write, long[]... loads) {
        long[] r0 = v.r.clone();
        int np = v.patchAddr.size();
        TcInputs.Vec nv = g.repairVec(v, fault, write, new ArrayList<>(Arrays.asList(loads)));
        if (nv == null) {
            System.out.println(name + ": null");
            return;
        }
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < 8; i++) if (nv.r[i] != r0[i]) b.append(" r").append(i + 3);
        for (int i = np; i < nv.patchAddr.size(); i++) b.append(" poke ").append(Long.toHexString(nv.patchAddr.get(i)));
        System.out.println(name + ":" + b);
    }

    public static void main(String[] args) {
        TcPrepare g = new TcPrepare();
        g.rnd = new Random(1);
        // Skies 8017cc70: lfs f1 of the f32 constant 24.0 at 8034b0ac (the image, no patch), then lfs f0,4(r30) with
        // r30 0 faults at 4. The constant explains no address: it is not an input.
        run(g, "image_constant", vec(), 0x4L, false, new long[] {0x8017ccecL, 0x8034b0acL, 4, 0x41c00000L});
        // Skies 80239108: a counted loop's stores run off RAM's end; the words it loaded are floats of our chunk, each
        // instruction's again on every pass.
        run(g, "runaway_store", vec(), 0x81800008L, true, new long[] {0x80239154L, 0x81001010L, 4, 0x42100000L},
            new long[] {0x8023915cL, 0x81001018L, 4, 0x46d3f6a8L}, new long[] {0x80239154L, 0x8100101cL, 4, 0x42100000L},
            new long[] {0x8023915cL, 0x81001024L, 4, 0x46d3f6a8L});
        // Skies 801559bc: lwz r29,0(r3) is the count of a loop of calls and stores, which runs off RAM's end. The count
        // is no term of the address; it is a word of ours read once.
        run(g, "count", vec(), 0x81800000L, true, new long[] {0x80155a18L, 0x81001024L, 4, 0x73555537L});
        // Skies 8019ae84: base + index with both unset: no base in RAM whichever is taken away.
        run(g, "two_unknowns", vec(), 0x3971503cL, false, new long[] {0x8019b024L, 0x81001030L, 4, 0x1a53b40eL},
            new long[] {0x8019b034L, 0x81001034L, 4, 0x1f1d9c2eL});
        // the same fault with a scripted call result near it: the result is the pointer (TcVectors.repair's).
        TcInputs.Vec c = vec();
        c.rets[2] = 0x39715000L;
        run(g, "call_result", c, 0x3971503cL, false, new long[] {0x8019b034L, 0x81001034L, 4, 0x1f1d9c2eL});
        // a guess is made GEN_BLIND times a vector, then no more.
        TcInputs.Vec b = vec();
        for (int k = 0; k < TcInputs.GEN_BLIND; k++) {
            run(g, "blind_" + k, b, 0x81800000L, true, new long[] {0x80155a18L, 0x81001040L + 4 * k, 4, 0x73555537L});
        }
        run(g, "blind_over", b, 0x81800000L, true, new long[] {0x80155a18L, 0x81001060L, 4, 0x73555537L});
        // a large register that is no pointer, when no loaded word is left.
        TcInputs.Vec q = vec();
        q.r[3] = 0x73555537L;
        run(g, "count_reg", q, 0x81800000L, true);
        // base + index * 4 with the index a word of our chunk: the sum the fallback is for.
        run(g, "sum_load", vec(), 0x81000100L + (0x00400000L << 2), false,
            new long[] {0x80001000L, 0x81001010L, 4, 0x00400000L});
        // the same with the index in r3, element size 8.
        TcInputs.Vec v = vec();
        v.r[0] = 0x00400000L;
        run(g, "sum_reg", v, 0x81000100L + (0x00400000L << 3), false);
        // Skies 80226dc4: one instruction walks an array of pointers in our chunk (stride 0x18), each word 0..15. One
        // repair gives the array's every element, from the first one read to the chunk's end, one shared chunk.
        TcInputs.Vec a = vec();
        put(a, 0x81001024L, 1);
        put(a, 0x8100103cL, 2);
        put(a, 0x81001054L, 3);
        run(g, "array_stride", a, 0xbL, false, new long[] {0x80226e08L, 0x81001024L, 4, 1},
            new long[] {0x80226e08L, 0x8100103cL, 4, 2}, new long[] {0x80226e08L, 0x81001054L, 4, 3});
        // the newest pointer was written by the run (our patch holds 0 there, it loaded 7): poking it changes nothing,
        // the older word of ours (patch and load agree) is the input.
        run(g, "own_write", vec(), 0x10L, false, new long[] {0x80226e10L, 0x81001090L, 4, 0},
            new long[] {0x80226e14L, 0x81001080L, 4, 7});
        // a word 0..15 of our chunk is tried as a null pointer first, then made a chunk when it is read again.
        TcInputs.Vec n = vec();
        put(n, 0x81001100L, 5);
        run(g, "null_first_0", n, 0xdL, false, new long[] {0x80226e20L, 0x81001100L, 4, 5});
        run(g, "null_first_1", n, 0x8L, false, new long[] {0x80226e20L, 0x81001100L, 4, 0});
        // a fault near address 0 is a null pointer: no blind guess (a count or sum runs off RAM's end, not to 0).
        TcInputs.Vec z = vec();
        for (int k = 0; k < 16; k++) z.rets[k] = TcBase.SCR;
        run(g, "blind_near_null", z, 0x10L, false, new long[] {0x80155a18L, 0x81001200L, 4, 0x73555537L});
        // the blind guess is 1, drawn from no generator.
        System.out.println("blind_values: " + Long.toHexString(b.genPoked.get(0x81001040L)) + " "
            + Long.toHexString(b.genPoked.get(0x81001044L)) + " " + Long.toHexString(b.genPoked.get(0x81001048L))
            + " " + Long.toHexString(q.r[3]));
    }
}
