/* The judged runtime interface tc_rt.h for the translator C in the port, with the hot half of it inline. txc includes
 * this file first (/FI in CMakeLists.txt), and it includes the judge's tc_rt.h (PORT_TC_RT_H) under renamed
 * declarations, so the translated file's own #include "tc_rt.h" finds the guard set. The judge's header declares every
 * call extern; here a guest memory access, fr0..fr15 and
 * r4..r14 are the same code as tx_rt.c's and sh4_rt.c's, in the caller, so a translated body's loads and stores are
 * not one call each (they were ~30% of a heavy frame). The translated C itself is unchanged: it names tc_rt.h and gets
 * the same functions, same results, same order. Anything with state or a side effect stays out of line:
 * a spin word's read (tx_rt.c rd32). */
#ifndef PORT_TX_TC_RT_INLINE_H
#define PORT_TX_TC_RT_INLINE_H

#include "port_config.h"

/* The judged header's declarations of what is defined inline below get other names here. */
#define rd8 tcx_decl_rd8
#define rd16 tcx_decl_rd16
#define rd32 tcx_decl_rd32
#define rdf32 tcx_decl_rdf32
#define wr8 tcx_decl_wr8
#define wr16 tcx_decl_wr16
#define wr32 tcx_decl_wr32
#define wrf32 tcx_decl_wrf32
#define sh4_fr tcx_decl_sh4_fr
#define sh4_set_fr tcx_decl_sh4_set_fr
#define sh4_xf tcx_decl_sh4_xf
#define sh4_set_xf tcx_decl_sh4_set_xf
#define sh4_r tcx_decl_sh4_r
#define sh4_set_r tcx_decl_sh4_set_r
#include PORT_TC_RT_H
#undef rd8
#undef rd16
#undef rd32
#undef rdf32
#undef wr8
#undef wr16
#undef wr32
#undef wrf32
#undef sh4_fr
#undef sh4_set_fr
#undef sh4_xf
#undef sh4_set_xf
#undef sh4_r
#undef sh4_set_r

extern u32 port_tx_spin_bits, port_tx_spin_mask; /* tx_spin.c: only an address these match can be a spin word (port_spin_word) */
u32 port_tx_rd32_slow(u32 addr);  /* tx_rt.c's rd32, spin words included */
extern u32 port_sh4_fr[16];       /* sh4_rt.c: fr0..fr15 as bits */
extern u32 port_sh4_xf[16];       /* sh4_rt.c: xf0..xf15 as bits */
extern u32 port_sh4_gs[7];        /* r8..r14 */
extern u32 port_sh4_ro[4];        /* r4..r7 as the last tc_call passed them */

#pragma warning(push)
#pragma warning(disable : 4505) /* an inline function a body does not use */

/* tx_rt.c's ABSENT: area 5 and P4's reserved space answer nothing, a read returns 0. */
#define TCX_AREA5(a) ((a) < 0xe0000000u && ((a) & 0x1c000000u) == 0x14000000u)
#define TCX_P4_RESERVED(a) ((a) >= 0xe4000000u && (a) < 0xf0000000u)
#define TCX_ABSENT(a) (TCX_AREA5(a) || TCX_P4_RESERVED(a))
/* game.json sq_queue_only: a store into the store-queue window is the translated C's own queue model's, not RAM's. */
#ifdef PORT_SQ_QUEUE_ONLY
#define TCX_SQ_WINDOW(a) ((a) - 0xe0000000u < 0x04000000u)
#else
#define TCX_SQ_WINDOW(a) 0
#endif

typedef union {
    u32 u;
    f32 f;
} TcxBits;

static __forceinline u8 rd8(u32 addr) { return TCX_ABSENT(addr) ? 0 : *(const volatile u8 *)(uintptr_t)addr; }
static __forceinline u16 rd16(u32 addr) { return TCX_ABSENT(addr) ? 0 : *(const volatile u16 *)(uintptr_t)addr; }
static __forceinline u32 rd32(u32 addr)
{
    if (!((addr ^ port_tx_spin_bits) & port_tx_spin_mask)) return port_tx_rd32_slow(addr);
    return TCX_ABSENT(addr) ? 0 : *(const volatile u32 *)(uintptr_t)addr;
}
static __forceinline f32 rdf32(u32 addr)
{
    TcxBits b;
    b.u = rd32(addr);
    return b.f;
}
static __forceinline void wr8(u32 addr, u8 value)
{
    if (TCX_SQ_WINDOW(addr)) return;
    *(volatile u8 *)(uintptr_t)addr = value;
}
static __forceinline void wr16(u32 addr, u16 value)
{
    if (TCX_SQ_WINDOW(addr)) return;
    *(volatile u16 *)(uintptr_t)addr = value;
}
static __forceinline void wr32(u32 addr, u32 value)
{
    if (TCX_SQ_WINDOW(addr)) return;
    *(volatile u32 *)(uintptr_t)addr = value;
}
static __forceinline void wrf32(u32 addr, f32 value)
{
    TcxBits b;
    b.f = value;
    wr32(addr, b.u);
}

static __forceinline f32 sh4_fr(int i)
{
    TcxBits b;
    if (i < 0 || i >= 16) return 0.0f;
    b.u = port_sh4_fr[i];
    return b.f;
}
static __forceinline void sh4_set_fr(int i, f32 v)
{
    TcxBits b;
    b.f = v;
    if (i >= 0 && i < 16) port_sh4_fr[i] = b.u;
}
/* Inline, because 32-bit x86 returns an f32 from a real call through x87: that sets the quiet bit of a signalling NaN.
 * A double-width fmov carries any bits through XMTRX: a movie's YUV pixel words with Y1=ff and V>=80 are such NaNs. */
static __forceinline f32 sh4_xf(int i)
{
    TcxBits b;
    if (i < 0 || i >= 16) return 0.0f;
    b.u = port_sh4_xf[i];
    return b.f;
}
static __forceinline void sh4_set_xf(int i, f32 v)
{
    TcxBits b;
    b.f = v;
    if (i >= 0 && i < 16) port_sh4_xf[i] = b.u;
}
static __forceinline u32 sh4_r(int i)
{
    return (i >= 8 && i < 15) ? port_sh4_gs[i - 8] : (i >= 4 && i < 8) ? port_sh4_ro[i - 4] : 0;
}
static __forceinline void sh4_set_r(int i, u32 v)
{
    if (i >= 8 && i < 15) port_sh4_gs[i - 8] = v;
    if (i >= 4 && i < 8) port_sh4_ro[i - 4] = v;
}

#pragma warning(pop)

#endif
