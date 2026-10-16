/* Guest RAM: one 16 MB shared section mapped at 0x8c000000 (P1) and again at its mirrors, 0x0c000000 (P0, MMU off)
 * and 0xac000000 (P2, uncached; Kamui keeps the vertex buffer's address in this form), so a guest pointer is a host
 * pointer (this is a 32-bit, large-address-aware process). The views are read/write but not executable: a call to a
 * guest code address faults and dispatch.c sends it to the host function. */
#include <string.h>
#include <windows.h>
#include "port.h"

static const u32 g_mirrors[] = {GUEST_MIRROR_BASE, GUEST_UNCACHED_BASE};
#define MIRROR_COUNT (sizeof g_mirrors / sizeof g_mirrors[0])

static int map_view(HANDLE sec, u32 base)
{
    void *at = (void *)(uintptr_t)base;
    if (MapViewOfFileEx(sec, FILE_MAP_ALL_ACCESS, 0, 0, GUEST_RAM_SIZE, at) != at) {
        port_log("guest RAM: cannot map at %08x (%lu)\n", base, GetLastError());
        return 0;
    }
    /* Just past each view: reserved, so a run off the end faults instead of touching host memory. */
    VirtualAlloc((void *)(uintptr_t)(base + GUEST_RAM_SIZE), 0x10000, MEM_RESERVE, PAGE_NOACCESS);
    return 1;
}

int guest_mem_init(void)
{
    HANDLE sec = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, GUEST_RAM_SIZE, NULL);
    if (!sec) {
        port_log("guest RAM: CreateFileMapping failed (%lu)\n", GetLastError());
        return 0;
    }
    if (!map_view(sec, GUEST_RAM_BASE)) return 0;
    for (u32 i = 0; i < MIRROR_COUNT; i++)
        if (!map_view(sec, g_mirrors[i])) return 0;
    port_log("guest RAM: 16 MB at %08x, mirrors at %08x and %08x\n", GUEST_RAM_BASE, g_mirrors[0], g_mirrors[1]);
    return 1;
}

u32 guest_fold(u32 addr)
{
    for (u32 i = 0; i < MIRROR_COUNT; i++)
        if (addr - g_mirrors[i] < GUEST_RAM_SIZE) return addr - g_mirrors[i] + GUEST_RAM_BASE;
    return addr;
}

int guest_is_ram(u32 addr) { return guest_fold(addr) - GUEST_RAM_BASE < GUEST_RAM_SIZE; }

int guest_load(u32 addr, const u8 *data, u32 size)
{
    addr = guest_fold(addr);
    if (addr < GUEST_RAM_BASE || size > GUEST_RAM_SIZE || addr - GUEST_RAM_BASE > GUEST_RAM_SIZE - size) return 0;
    memcpy((void *)(uintptr_t)addr, data, size);
    return 1;
}
