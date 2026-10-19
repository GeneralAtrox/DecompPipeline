// Float callee results (docs/PROOF_GATES.md). A mocked callee's scripted result goes to r0 and fr0 alike, and the
// inputs draw small integers and pointers there (TcInputs: 0..3 for the first calls). As f32 bits those are
// denormals or tiny negatives, so a callee returning f32 always handed the caller almost 0.0 and no comparison
// against it was ever tested: TC 8c035a0c compares 8c0612e4's f32 result with a pool constant (1e-06), and C
// with 1.0 there passed every vector. For a callee whose prototype returns f32 (TcPrepare: TcOracle.floatRet), a
// result that is not a plain number becomes one of PLAIN, picked by its bits (2 and 3 give 0.5 and -1). Kept as
// drawn: plain numbers, 0, the denormal 1 and scratch pointers (tiny negatives), so the paths for a tiny result
// stay tested (fixtures tc-fpscr-dn-8c033f22: fsqrt of a denormal; tc-fr-through-call-8c03a2f6: |result| under a
// threshold). The vector keeps the new value (TcOracle writes v.rets back), so vectors.txt and the C runner use the
// same bits, and reruns (rescue, reach) see a kept value again. A callee returning u32 is unchanged.

final class TcFret {
    static final float[] PLAIN = {0f, 1f, 0.5f, -1f, 2f, 1e-3f, 1e-7f, -0.5f, 10f, 100f, 0.25f, -2f, 1e-5f, 0.9f,
        1.5f, 1000f};

    private TcFret() {
    }

    // rv as it is (+-0, a finite magnitude between about 1e-30 and 1e30, the denormal 1, a scratch pointer), else
    // picked from PLAIN.
    static long of(long rv) {
        rv &= 0xffffffffL;
        int e = (int) ((rv >> 23) & 0xff);
        if ((rv & 0x7fffffffL) == 0 || (e >= 27 && e <= 227) || rv == 1 || (rv >>> 20) == 0x8ccL) return rv;
        return Float.floatToRawIntBits(PLAIN[(int) ((rv ^ (rv >>> 16)) & 15)]) & 0xffffffffL;
    }

    // The result of the mocked call to callee: of(rv) when the callee returns f32, else rv.
    static long at(java.util.Set<Long> floatRet, long callee, long rv) {
        return floatRet.contains(callee) ? of(rv) : rv;
    }
}
