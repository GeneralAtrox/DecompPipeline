/* Guest RAM: one 16 MB shared section mapped at 0x8c000000 (P1) and again at its mirrors, 0x0c000000 (P0, MMU off),
 * 0xac000000 (P2, uncached; Kamui keeps the vertex buffer's address in this form) and the four views of the
 * store-queue window at 0xe0000000 (port.h GUEST_SQ_BASE), so a guest pointer is a host pointer (this is a 32-bit,
 * large-address-aware process). The views are read/write but not executable: a call to a
 * guest code address faults and dispatch.c sends it to the host function. The boot image's code pages in P1 are
 * the exception once dispatch.c has put its jump thunks there. The P4 control registers at 0xff000000 (port.h
 * GUEST_P4_CCN) are one more read/write block of their own. */
#include <string.h>
#include <windows.h>
#include "port.h"

static const u32 g_mirrors[] = {GUEST_MIRROR_BASE, GUEST_UNCACHED_BASE, GUEST_SQ_BASE,
                                 GUEST_SQ_BASE + GUEST_RAM_SIZE, GUEST_SQ_BASE + 2u * GUEST_RAM_SIZE,
                                 GUEST_SQ_BASE + 3u * GUEST_RAM_SIZE};
#define MIRROR_COUNT (sizeof g_mirrors / sizeof g_mirrors[0])
#define OC_ADDR_ARRAY 0xf4000000u /* SH-4 operand cache address array: 512 entries 0x20 apart, plus the A bit */
#define OC_ADDR_ARRAY_SIZE 0x10000u

static int map_view(HANDLE sec, u32 base)
{
    void *at = (void *)(uintptr_t)base;
    DWORD old;
    /* P1 may hold executable pages later (dispatch.c's thunks): mapped so it can, then made read/write */
    DWORD access = base == GUEST_RAM_BASE ? FILE_MAP_ALL_ACCESS | FILE_MAP_EXECUTE : FILE_MAP_ALL_ACCESS;
    if (MapViewOfFileEx(sec, access, 0, 0, GUEST_RAM_SIZE, at) != at
        || (base == GUEST_RAM_BASE && !VirtualProtect(at, GUEST_RAM_SIZE, PAGE_READWRITE, &old))) {
        port_log("guest RAM: cannot map at %08x (%lu)\n", base, GetLastError());
        return 0;
    }
    /* Just past each view: reserved, so a run off the end faults instead of touching host memory (the store-queue
     * views are back to back, so only the last one has a guard). */
    if (base < GUEST_SQ_BASE || base == GUEST_SQ_BASE + (GUEST_SQ_VIEWS - 1u) * GUEST_RAM_SIZE)
        VirtualAlloc((void *)(uintptr_t)(base + GUEST_RAM_SIZE), 0x10000, MEM_RESERVE, PAGE_NOACCESS);
    return 1;
}

int guest_mem_init(void)
{
    HANDLE sec = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_EXECUTE_READWRITE, 0, GUEST_RAM_SIZE, NULL);
    if (!sec) {
        port_log("guest RAM: CreateFileMapping failed (%lu)\n", GetLastError());
        return 0;
    }
    if (!map_view(sec, GUEST_RAM_BASE)) return 0;
    for (u32 i = 0; i < MIRROR_COUNT; i++)
        if (!map_view(sec, g_mirrors[i])) return 0;
    /* The operand cache's address array: a write there invalidates a cache line (a movie player clears it after each
     * decode), which means nothing on the host, so its entries are scratch memory. */
    if (!VirtualAlloc((void *)(uintptr_t)OC_ADDR_ARRAY, OC_ADDR_ARRAY_SIZE, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE))
        port_log("guest RAM: cannot map the operand cache address array at %08x (%lu)\n", OC_ADDR_ARRAY,
                 GetLastError());
    port_log("guest RAM: 16 MB at %08x, mirrors at %08x and %08x, store-queue window %08x..%08x\n", GUEST_RAM_BASE,
             g_mirrors[0], g_mirrors[1], GUEST_SQ_BASE, GUEST_SQ_BASE + GUEST_SQ_VIEWS * GUEST_RAM_SIZE - 1u);
    void *p4 = (void *)(uintptr_t)GUEST_P4_CCN;
    if (VirtualAlloc(p4, 0x10000, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE) != p4) {
        port_log("P4 registers: cannot map %08x (%lu)\n", GUEST_P4_CCN, GetLastError());
        return 0;
    }
    return 1;
}

void guest_p4_report(void)
{
    u32 q0 = *(volatile u32 *)(uintptr_t)GUEST_P4_QACR0, q1 = *(volatile u32 *)(uintptr_t)GUEST_P4_QACR1;
    if (!q0 && !q1) return; /* never written */
    port_log("P4 QACR0 %08x QACR1 %08x%s\n", q0, q1,
             ((q0 >> 2) & 7u) == 3u && ((q1 >> 2) & 7u) == 3u ? " (area 3, as the store-queue window)"
                                                            : ": not area 3; the store-queue window still writes area 3");
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
