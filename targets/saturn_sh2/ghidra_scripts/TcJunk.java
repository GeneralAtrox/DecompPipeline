// The stricter judge's one random stream (TcStrict; target.json "mock_junk" and "mock_writes"): what a mocked callee
// leaves in the scratch registers and writes through the pointers it is known to write through. splitmix64's finalizer
// (Steele, Lea and Flood 2014: add the golden gamma 0x9e3779b97f4a7c15, then two xor-shift-multiply rounds and a
// last xor-shift), chained over the key: at(seed, vid, call, slot) = mix(mix(mix(mix(seed) ^ vid) ^ call) ^ slot),
// seed the function seed (TcPrepare), vid the vector's key (vectors.txt line N), call the mocked call's index from 0,
// slot what the value is for (TcStrict: a register, or 8 bytes of a written range). No state: the same key gives the
// same value in any order, so a repaired, rerun or renumbered vector carries its key, not its position. The C
// runtimes' copy is runtime/tc_strict.h (tcs_mix, tcs_at); tests/test_strict_judge.py runs both on the same keys.
// Not a script itself; the same file in every target.
final class TcJunk {
    private TcJunk() {
    }

    static long mix(long z) {
        z += 0x9e3779b97f4a7c15L;
        z = (z ^ (z >>> 30)) * 0xbf58476d1ce4e5b9L;
        z = (z ^ (z >>> 27)) * 0x94d049bb133111ebL;
        return z ^ (z >>> 31);
    }

    static long at(long seed, long vid, long call, long slot) {
        return mix(mix(mix(mix(seed) ^ vid) ^ call) ^ slot);
    }

    // A float register's junk (TcStrict, tc_strict.h tcs_fr): the sign and fraction of the value's low 32 bits with
    // 1.0's exponent, +-[1, 2): its product with a normal float is normal. Raw bits made NaNs, infinities and extremes:
    // Toy Commander 8c06161c multiplies two junk matrices (ftrv), and 68 of its runs hit a denormal the oracle refuses.
    static long fr(long z) {
        return z & 0x807fffffL | 0x3f800000L;
    }

    // Byte k of a written range whose stream starts at slot: byte k % 8 (least significant first) of value k / 8.
    static int at8(long seed, long vid, long call, long slot, int k) {
        return (int) (at(seed, vid, call, slot + (k >>> 3)) >>> (8 * (k & 7))) & 0xff;
    }
}
