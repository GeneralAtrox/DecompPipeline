/* Platform layer: zero-filled stack frames for the readable C (CMakeLists.txt PORT_STACK_ZERO, which compiles it
 * with /Gh). The readable C runs natively, so its locals live on the host stack: a local that the original leaves
 * partly unset (a struct a callee fills only in part, then copied whole into guest RAM) would otherwise carry
 * whatever the host left there - return addresses, saved frame pointers and the run's own /GS cookie, which differs
 * from run to run. /Gh calls _penter first thing in every function; it zeroes the frame the function is about to
 * take, so every new frame starts out the same, run after run.
 *
 * The frame's size comes from the function's own prologue (MSVC x86 /O1 and /Od): its register pushes, an
 * `and esp` alignment, and `sub esp` or `mov eax, N; call __chkstk` for the locals. A __chkstk frame (over a page)
 * still gets __chkstk's own return address and saved ecx in its top 8 bytes, which it writes after _penter. */
#include <stdint.h>

#define PAGE 0x1000u
#define PROLOGUE_BYTES 32u     /* no prologue is longer before its locals are allocated */
#define ALIGN_ROOM 16u         /* what an `and esp, -16` can add below the pushes */
#define SAVE_ROOM 20u          /* _penter's four registers and the return slot's address, below the frame */

static uint32_t imm32(const uint8_t *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

/* The bytes the function at `code` (just after its call to _penter) takes below `top` (the stack slot holding
 * _penter's return address, which becomes the function's first push), rounded up to whole words. Pages below `top`
 * (and _penter's save room below them) are touched top down first, as __chkstk does, so the zeroing may then run
 * upwards through them. */
static uint32_t __cdecl stack_zero_bytes(const uint8_t *code, const uint8_t *top)
{
    uint32_t n = 4, i = 0, p;
    while (i < PROLOGUE_BYTES) {
        const uint8_t *c = code + i;
        if (c[0] >= 0x50 && c[0] <= 0x57) {                       /* push r32 */
            n += 4; i += 1;
        } else if (c[0] == 0x8b && (c[1] == 0xec || c[1] == 0xdc)) { /* mov ebp, esp / mov ebx, esp */
            i += 2;
        } else if (c[0] == 0x83 && c[1] == 0xe4) {                 /* and esp, imm8 */
            n += ALIGN_ROOM; i += 3;
        } else if (c[0] == 0x83 && c[1] == 0xec) {                 /* sub esp, imm8 */
            n += c[2]; i += 3;
        } else if (c[0] == 0x81 && c[1] == 0xec) {                 /* sub esp, imm32 */
            n += imm32(c + 2); i += 6;
        } else if (c[0] == 0xb8 && c[5] == 0xe8) {                 /* mov eax, imm32; call __chkstk */
            n += imm32(c + 1);
            break;
        } else {
            break;
        }
    }
    n = (n + 3u) & ~3u;
    for (p = PAGE; p < n + SAVE_ROOM; p += PAGE) (void)*(volatile const uint8_t *)(top - p);
    (void)*(volatile const uint8_t *)(top - n - SAVE_ROOM);
    return n;
}

/* /Gh's hook. It keeps every register, the xmm ones too (a caller may keep a value in one across a call to a function
 * the compiler knows does not use it). The registers wait just below the n bytes being cleared, so nothing of the hook
 * stays in the new frame. */
__declspec(naked) void __cdecl _penter(void)
{
    __asm {
        push eax
        push ecx
        push edx
        push edi
        lea eax, [esp + 16]
        push eax
        push dword ptr [eax]
        call stack_zero_bytes
        add esp, 8
        lea ecx, [esp + 16]
        sub ecx, eax
        sub ecx, 20
        mov edx, [esp]
        mov [ecx], edx
        mov edx, [esp + 4]
        mov [ecx + 4], edx
        mov edx, [esp + 8]
        mov [ecx + 8], edx
        mov edx, [esp + 12]
        mov [ecx + 12], edx
        lea edx, [esp + 16]
        mov [ecx + 16], edx
        mov esp, ecx
        mov edi, edx
        sub edi, eax
        mov ecx, eax
        shr ecx, 2
        xor eax, eax
        rep stosd
        pop edi
        pop edx
        pop ecx
        pop eax
        pop esp
        ret
    }
}
