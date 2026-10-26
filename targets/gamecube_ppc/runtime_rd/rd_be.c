/* Big-endian guest memory for readable C (rd_be.h). Every access is a plain load or store of the whole value at its
 * own address (one access, as the guest's), with the bytes reversed. */
#include <stdlib.h>
#include <string.h>
#include "rd_be.h"

static uint16_t sw16(uint16_t v) { return _byteswap_ushort(v); }
static uint32_t sw32(uint32_t v) { return _byteswap_ulong(v); }
static uint64_t sw64(uint64_t v) { return _byteswap_uint64(v); }

uint16_t be_get_u16(const be_u16 *p) { return sw16(*(const volatile uint16_t *)&p->be_bits); }
int16_t be_get_s16(const be_s16 *p) { return (int16_t)sw16(*(const volatile uint16_t *)&p->be_bits); }
uint32_t be_get_u32(const be_u32 *p) { return sw32(*(const volatile uint32_t *)&p->be_bits); }
int32_t be_get_s32(const be_s32 *p) { return (int32_t)sw32(*(const volatile uint32_t *)&p->be_bits); }
uint64_t be_get_u64(const be_u64 *p) { return sw64(*(const volatile uint64_t *)&p->be_bits); }
int64_t be_get_s64(const be_s64 *p) { return (int64_t)sw64(*(const volatile uint64_t *)&p->be_bits); }

float be_get_f32(const be_f32 *p)
{
    uint32_t u = sw32(*(const volatile uint32_t *)&p->be_bits);
    float f;
    memcpy(&f, &u, 4);
    return f;
}

double be_get_f64(const be_f64 *p)
{
    uint64_t u = sw64(*(const volatile uint64_t *)&p->be_bits);
    double d;
    memcpy(&d, &u, 8);
    return d;
}

void *be_get_ptr(const be_ptr *p) { return (void *)(uintptr_t)sw32(*(const volatile uint32_t *)&p->be_bits); }

void (*be_get_fnptr(const be_fnptr *p))(void)
{
    return (void (*)(void))(uintptr_t)sw32(*(const volatile uint32_t *)&p->be_bits);
}

void be_set_u16(be_u16 *p, uint16_t v) { *(volatile uint16_t *)&p->be_bits = sw16(v); }
void be_set_s16(be_s16 *p, int16_t v) { *(volatile uint16_t *)&p->be_bits = sw16((uint16_t)v); }
void be_set_u32(be_u32 *p, uint32_t v) { *(volatile uint32_t *)&p->be_bits = sw32(v); }
void be_set_s32(be_s32 *p, int32_t v) { *(volatile uint32_t *)&p->be_bits = sw32((uint32_t)v); }
void be_set_u64(be_u64 *p, uint64_t v) { *(volatile uint64_t *)&p->be_bits = sw64(v); }
void be_set_s64(be_s64 *p, int64_t v) { *(volatile uint64_t *)&p->be_bits = sw64((uint64_t)v); }

void be_set_f32(be_f32 *p, float v)
{
    uint32_t u;
    memcpy(&u, &v, 4);
    *(volatile uint32_t *)&p->be_bits = sw32(u);
}

void be_set_f64(be_f64 *p, double v)
{
    uint64_t u;
    memcpy(&u, &v, 8);
    *(volatile uint64_t *)&p->be_bits = sw64(u);
}

void be_set_ptr(be_ptr *p, const void *v) { *(volatile uint32_t *)&p->be_bits = sw32((uint32_t)(uintptr_t)v); }
void be_set_fnptr(be_fnptr *p, void (*v)(void)) { *(volatile uint32_t *)&p->be_bits = sw32((uint32_t)(uintptr_t)v); }

uint32_t be_bits32(const void *p) { return sw32(*(const volatile uint32_t *)p); }
void be_set_bits32(void *p, uint32_t bits) { *(volatile uint32_t *)p = sw32(bits); }
