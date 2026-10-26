/* Fixed-width types for original-style C compiled by CodeWarrior (mwcceppc) in the byte-match check
 * (targets/gamecube_ppc/ppc_match.py). The Dolphin SDK's dolphin/types.h spells them the same way. */
#ifndef GC_TYPES_H
#define GC_TYPES_H
typedef signed char s8;
typedef unsigned char u8;
typedef signed short s16;
typedef unsigned short u16;
typedef signed long s32;
typedef unsigned long u32;
typedef signed long long s64;
typedef unsigned long long u64;
typedef float f32;
typedef double f64;
typedef int BOOL;
#ifndef NULL
#define NULL 0
#endif
#endif
