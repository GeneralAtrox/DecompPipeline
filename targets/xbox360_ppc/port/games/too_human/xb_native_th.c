/* Too Human: native rewrites of translated game functions. gen_xbox.py compiles each gen/fn_ADDR.c that native.json lists as fn_ADDR_tx, so the
 * game's calls reach the function here, which runs the native body or the translated one (the answer key).
 *
 * fn_82a03608 (thread 82a041b8, 20 ms of a 71 ms gameplay frame on build box A, 2026-11-15): 64 rows of four floats at r3 each take
 * vmaddfp(row at r4, gain, row): dst += src * gain, the gain a per-lane ramp from the vectors at r5 (start) and r6 (step), four chains
 * each stepping by 4 * step, all by vaddfp. The translated helpers run each lane through an exact single-rounding multiply-add in doubles
 * with a NaN test per lane (xv_fma1), the main cost. Here: SSE with FMA3 (_mm_fmadd_ps rounds once, as vmaddfp), _mm_add_ps for vaddfp,
 * denormal inputs and results flushed to zero by MXCSR DAZ and FTZ for the call (as xv_wf / xv_fw), guest words byte-swapped in place.
 * The results are made in registers first and stored only when no lane is NaN: a NaN anywhere (an input, inf * 0, inf - inf) or source and
 * destination rows overlapping sends the call to the translated body, whose NaN choice (a, then c, then b, else the default) x86 does not
 * follow. The vector registers the translated body leaves (v0..v13, v29..v31 in tc_vr) and its stack word are written the same.
 *
 * XB_NATIVE_82A03608: on by default; =0 the translated route; =c both, the translated result kept, and every call whose 1 KB or registers
 * differ counted (logged every 100 frames). A host without FMA3, SSSE3 or the OS's AVX state takes the translated route.
 * tests/test_x360_native_82a03608.py compares the two routes byte for byte on denormals, NaNs, infinities and rounding ties.
 * Nearest existing: runtime/xv_simd.h, the translator's per-helper SSE (vmx.SIMD) - it covers integer and permute helpers, not the float
 * multiply-add, and a helper at a time cannot keep the rows and gains in registers across the loop. */
#include <windows.h>
#include <stdlib.h>
#include <string.h>
#include <intrin.h>
#include <immintrin.h>
#include "tc_rt.h"

extern __declspec(thread) u32 tc_sp;
extern __declspec(thread) u32 tc_vr[128][4];
void port_log(const char *fmt, ...);
unsigned xb_frame_number(void);
u64 fn_82a03608_tx(u64 arg_r3, u64 arg_r4, u64 arg_r5, u64 arg_r6);

static int s_n03608 = -1;   /* 0 translated, 1 native, 2 check */
static LONG64 s_n_calls, s_n_native, s_n_back, s_n_diff, s_n_diff_bytes;
static volatile LONG s_n_window = -1;

static int host_fma(void)
{
    int r[4];
    __cpuid(r, 1);
    if (!((r[2] >> 12) & 1) || !((r[2] >> 9) & 1) || !((r[2] >> 27) & 1) || !((r[2] >> 28) & 1)) return 0;   /* FMA, SSSE3, OSXSAVE, AVX */
    return (_xgetbv(0) & 6) == 6;
}

static int n_mode(void)
{
    if (s_n03608 < 0) {
        const char *e = getenv("XB_NATIVE_82A03608");
        int m = e && e[0] == '0' ? 0 : e && (e[0] == 'c' || e[0] == '2') ? 2 : 1, fma = host_fma();
        if (m && !fma) m = 0;
        port_log("native 82a03608 (XB_NATIVE_82A03608): %s%s\n", m == 0 ? "translated route" : m == 1 ? "native" : "check, both routes, the translated result kept",
                 fma ? "" : " (the host has no FMA3 / SSSE3 / AVX state)");
        s_n03608 = m;
    }
    return s_n03608;
}

#define N_SWAP _mm_setr_epi8(3, 2, 1, 0, 7, 6, 5, 4, 11, 10, 9, 8, 15, 14, 13, 12)
static __forceinline __m128 n_ld(u32 ea) { return _mm_castsi128_ps(_mm_shuffle_epi8(_mm_loadu_si128((const __m128i *)(tc_base + (ea & 0xfffffff0u))), N_SWAP)); }
static __forceinline __m128i n_be(__m128 v) { return _mm_shuffle_epi8(_mm_castps_si128(v), N_SWAP); }

/* The 64 result rows (guest byte order, row 4i+k at +64i+16k) and registers v0..v31 (lane values as tc_vr holds them): 1 when made, 0 for the
 * translated body (a NaN, or the rows overlap). Guest memory is not written */
static int n_run(u64 r3, u64 r4, u64 r5, u64 r6, __m128i rows[64], __m128 vr[32])
{
    u32 a = (u32)r3 & 0xfffffff0u, b = (u32)r4 & 0xfffffff0u, i, o;
    unsigned mx;
    __m128 v0, v1 = _mm_setzero_ps(), v2 = v1, v3 = v1, v4 = v1, v5 = v1, v6 = v1, v7 = v1, v8 = v1, v9, v10, v11, v12, v13, v29 = v1, v30 = v1, v31 = v1, un = v1;
    if ((u32)(a - b) < 0x400u || (u32)(b - a) < 0x400u) return 0;   /* source rows inside the destination's 1 KB: the order of loads and stores matters */
    mx = _mm_getcsr();
    _mm_setcsr((mx & ~0x6000u) | 0x8040u);   /* round to nearest, FTZ, DAZ */
    v0 = n_ld((u32)r6);
    v13 = _mm_add_ps(v0, v0);
    v12 = n_ld((u32)r5);
    v11 = _mm_add_ps(v12, v0);
    v9 = _mm_add_ps(v13, v0);
    v0 = _mm_add_ps(v13, v13);
    v10 = _mm_add_ps(v12, v13);
    v13 = _mm_add_ps(v12, v9);
    for (i = 0; i < 16; i++) {
        o = 64u * i;
        v9 = n_ld(a + o + 0x30u), v8 = n_ld(b + o + 0x30u), v7 = n_ld(b + o + 0x20u), v6 = n_ld(b + o), v4 = n_ld(b + o + 0x10u);
        v3 = n_ld(a + o), v2 = n_ld(a + o + 0x10u), v31 = n_ld(a + o + 0x20u);
        v5 = _mm_fmadd_ps(v8, v13, v9);
        v13 = _mm_add_ps(v13, v0);
        v1 = _mm_fmadd_ps(v6, v12, v3);
        v30 = _mm_fmadd_ps(v4, v11, v2);
        v29 = _mm_fmadd_ps(v7, v10, v31);
        v12 = _mm_add_ps(v12, v0), v11 = _mm_add_ps(v11, v0), v10 = _mm_add_ps(v10, v0);
        un = _mm_or_ps(un, _mm_or_ps(_mm_cmpunord_ps(v1, v30), _mm_cmpunord_ps(v29, v5)));
        rows[4 * i] = n_be(v1), rows[4 * i + 1] = n_be(v30), rows[4 * i + 2] = n_be(v29), rows[4 * i + 3] = n_be(v5);
    }
    un = _mm_or_ps(un, _mm_or_ps(_mm_or_ps(_mm_cmpunord_ps(v0, v13), _mm_cmpunord_ps(v12, v11)), _mm_cmpunord_ps(v10, v10)));
    _mm_setcsr(mx);
    if (_mm_movemask_ps(un)) return 0;
    vr[0] = v0, vr[1] = v1, vr[2] = v2, vr[3] = v3, vr[4] = v4, vr[5] = v5, vr[6] = v6, vr[7] = v7, vr[8] = v8, vr[9] = v9;
    vr[10] = v10, vr[11] = v11, vr[12] = v12, vr[13] = v13, vr[29] = v29, vr[30] = v30, vr[31] = v31;
    return 1;
}

static const unsigned char s_vr_set[] = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 29, 30, 31};

static void n_report(void)
{
    LONG w = (LONG)(xb_frame_number() / 100u);
    if (w != s_n_window && InterlockedExchange(&s_n_window, w) != w)
        port_log("native 82a03608 at frame %u: %lld calls, %lld native, %lld by the translated body (NaN or overlap); check: %lld calls differ, %lld bytes\n", xb_frame_number(),
                 s_n_calls, s_n_native, s_n_back, s_n_diff, s_n_diff_bytes);
}

u64 fn_82a03608(u64 arg_r3, u64 arg_r4, u64 arg_r5, u64 arg_r6)
{
    __m128i rows[64];
    __m128 vr[32];
    int m = n_mode(), made;
    unsigned k, d;
    if (!m) return fn_82a03608_tx(arg_r3, arg_r4, arg_r5, arg_r6);
    InterlockedIncrement64(&s_n_calls);
    made = n_run(arg_r3, arg_r4, arg_r5, arg_r6, rows, vr);
    if (!made || m == 2) {
        u64 r = fn_82a03608_tx(arg_r3, arg_r4, arg_r5, arg_r6);
        if (!made) InterlockedIncrement64(&s_n_back);
        else {   /* check: the native rows and registers against what the translated body wrote */
            u8 *dst = tc_base + ((u32)arg_r3 & 0xfffffff0u);
            for (d = 0, k = 0; k < 64; k++) {
                const u8 *p = (const u8 *)&rows[k], *q = dst + 16u * k;
                unsigned j;
                for (j = 0; j < 16; j++) d += p[j] != q[j];
            }
            for (k = 0; k < sizeof s_vr_set; k++) d += memcmp(&vr[s_vr_set[k]], tc_vr[s_vr_set[k]], 16) != 0;
            if (d) {
                if (InterlockedIncrement64(&s_n_diff) <= 4)
                    port_log("native 82a03608 check: r3 %08x r4 %08x gain %08x step %08x: %u bytes or registers differ\n", (u32)arg_r3, (u32)arg_r4, (u32)arg_r5, (u32)arg_r6, d);
                InterlockedAdd64(&s_n_diff_bytes, d);
            }
            InterlockedIncrement64(&s_n_native);
        }
        n_report();
        return r;
    }
    wr64(tc_sp - 8u, 0ull);
    {
        u8 *dst = tc_base + ((u32)arg_r3 & 0xfffffff0u);
        for (k = 0; k < 64; k++) _mm_storeu_si128((__m128i *)(dst + 16u * k), rows[k]);
    }
    for (k = 0; k < sizeof s_vr_set; k++) _mm_storeu_si128((__m128i *)tc_vr[s_vr_set[k]], _mm_castps_si128(vr[s_vr_set[k]]));
    InterlockedIncrement64(&s_n_native);
    n_report();
    return 0xffffffffffffffe0ull;
}
