/* Big-endian guest memory for readable C (the Saturn and GameCube targets keep identical copies of this file and of
 * rd_be.c; tests/test_readable_be.py checks that they agree).
 *
 * The guest's RAM is mapped at its own address, so a guest pointer is a host pointer, but the guest stores every
 * value of 2, 4 or 8 bytes most significant byte first and the host reads least significant byte first. A plain
 * u32 field would read the bytes reversed. So every multi-byte value in guest memory (a struct field, a global, an
 * array element, a pointer stored in memory) has one of the be_ types below, and is read and written only through
 * be_get and be_set. Each be_ type is its own struct, so using one as a number is a compile error, never a silent
 * byte swap. u8, s8 and byte arrays (strings) need nothing.
 *
 *   s32 hp = be_get(actor->hp);          be_set(actor->hp, hp - damage);
 *   Actor *next = be_get(actor->next);   (a BE_PTR(Actor) field; be_get gives a void *)
 *   ((ActorFn)be_get(actor->update))(actor);   (a be_fnptr field: a call through it is a call to the guest code)
 *
 * Locals, parameters and results are plain host values (u32, f32, Actor *): only memory has the guest byte order.
 * The sizes and alignments: be_u16 2, be_u32/be_f32/be_ptr 4, be_u64/be_f64 8 and 8-aligned in a struct (as on the
 * PowerPC, and as MSVC lays out a 32-bit build). Where the original keeps an 8-byte value only 4-aligned (SH-2 code
 * has few: no FPU), write it as two be_u32 (the high word first).
 */
#ifndef RD_BE_H
#define RD_BE_H

#include <stdint.h>

typedef struct { uint16_t be_bits; } be_u16;
typedef struct { uint16_t be_bits; } be_s16;
typedef struct { uint32_t be_bits; } be_u32;
typedef struct { uint32_t be_bits; } be_s32;
typedef struct { uint32_t be_bits; } be_f32;
typedef struct { uint32_t be_bits; } be_ptr;   /* a data pointer stored in guest memory */
typedef struct { uint32_t be_bits; } be_fnptr; /* a code pointer stored in guest memory */
typedef struct { uint64_t be_bits; } be_u64;
typedef struct { uint64_t be_bits; } be_s64;
typedef struct { uint64_t be_bits; } be_f64;
/* A pointer field that says what it points at (the header's documentation; it is a be_ptr). */
#define BE_PTR(T) be_ptr

uint16_t be_get_u16(const be_u16 *p);
int16_t be_get_s16(const be_s16 *p);
uint32_t be_get_u32(const be_u32 *p);
int32_t be_get_s32(const be_s32 *p);
float be_get_f32(const be_f32 *p);
void *be_get_ptr(const be_ptr *p);
void (*be_get_fnptr(const be_fnptr *p))(void);
uint64_t be_get_u64(const be_u64 *p);
int64_t be_get_s64(const be_s64 *p);
double be_get_f64(const be_f64 *p);
void be_set_u16(be_u16 *p, uint16_t v);
void be_set_s16(be_s16 *p, int16_t v);
void be_set_u32(be_u32 *p, uint32_t v);
void be_set_s32(be_s32 *p, int32_t v);
void be_set_f32(be_f32 *p, float v);
void be_set_ptr(be_ptr *p, const void *v);
void be_set_fnptr(be_fnptr *p, void (*v)(void));
void be_set_u64(be_u64 *p, uint64_t v);
void be_set_s64(be_s64 *p, int64_t v);
void be_set_f64(be_f64 *p, double v);
/* The bits of a 4- or 8-byte value, for code that moves a value it does not interpret. */
uint32_t be_bits32(const void *p);
void be_set_bits32(void *p, uint32_t bits);

#define be_get(x) _Generic((x), \
    be_u16: be_get_u16, be_s16: be_get_s16, be_u32: be_get_u32, be_s32: be_get_s32, be_f32: be_get_f32, \
    be_ptr: be_get_ptr, be_fnptr: be_get_fnptr, be_u64: be_get_u64, be_s64: be_get_s64, be_f64: be_get_f64)(&(x))
#define be_set(x, v) _Generic((x), \
    be_u16: be_set_u16, be_s16: be_set_s16, be_u32: be_set_u32, be_s32: be_set_s32, be_f32: be_set_f32, \
    be_ptr: be_set_ptr, be_fnptr: be_set_fnptr, be_u64: be_set_u64, be_s64: be_set_s64, be_f64: be_set_f64)(&(x), (v))

#endif
