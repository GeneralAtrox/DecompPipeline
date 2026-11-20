/* xb_imports.c, the kernel's imports (one translation unit with it, for its helpers; here for the 400-line cap) */
void xb_d3d_guest_freed(u32 at, u32 size);   /* xb_d3d_cb.h: a command buffer in a block the game gave back is retired */

/* xb_free_block, and the kit's command buffers in the block told (their chunks go back to the kit's pool) */
static u32 free_block_told(u32 at)
{
    u32 n = xb_free_block(at);
    if (n) xb_d3d_guest_freed(at, n);
    return n;
}

static int krnl_call(const XbImp *imp, const TcArgs *a, u64 *r3, f64 *f1)
{
    (void)f1;
    (void)imp;
    switch (imp->ord) {
    case 310:     /* RtlRaiseException(EXCEPTION_RECORD *): a C++ throw is walked on the game's tables (xb_eh.h); any other raise stops as before */
        return xb_eh_raise(a);
    case 204: {   /* NtAllocateVirtualMemory(u32 *base, u32 *size, type, protect): first stop, 822bb160's caller: r3, r4 stack
                   * addresses, r5 0x60002000, r6 4 (PAGE_READWRITE) */
        u32 pb = (u32)a->r[0], ps = (u32)a->r[1], want = rd32(pb), size = rd32(ps);
        if (!want)   /* MEM_LARGE_PAGES (0x20000000) in the type: from the console's 64 KB-page range, which Too Human's heap tells apart by address */
            want = ((u32)a->r[2] & 0x20000000u ? xb_alloc_block_large : xb_alloc_block)((size + 0xFFFFu) & ~0xFFFFu, 0x10000u);   /* a request for a given base is already in the reserve: the game keeps it */
        wr32(pb, want);
        wr32(ps, (size + 0xFFFFu) & ~0xFFFFu);
        *r3 = 0;
        return 1;
    }
    case 302: {   /* RtlInitializeCriticalSection(CRITICAL_SECTION *): r3 the address of a 28-byte structure inside a game object
                   * (obj+4, obj+12 ...), 34 call sites in 21 functions; first stop: r3=20000618 */
        (void)cs_of((u32)a->r[0]);
        return 1;
    }
    case 293:     /* RtlEnterCriticalSection(CRITICAL_SECTION *): 233 call sites in 225 functions, one pointer (often a static in
                   * .data, first stop r3=824b08c0), each paired with the thunk after it (287 sites, more exits) */
    {
        CRITICAL_SECTION *c = cs_of((u32)a->r[0]);
        for (;;) {
            if (!TryEnterCriticalSection(c)) {
                u32 *w = cs_wait_slot();
                if (w) *w = (u32)a->r[0];
                {
                    unsigned long owner = (unsigned long)(ULONG_PTR)c->OwningThread;
                    LONG64 t0 = ws_start();
                    EnterCriticalSection(c);
                    ws_waited(t0, 0, owner);
                }
                if (w) *w = 0;
            }
            /* Suspended while it waited (Too Human's main thread suspends a pool worker 829b89e8 waiting for the pool's lock, then takes the
             * lock itself): the console's thread would still be waiting, so this one lets the lock go and stops until it is resumed */
            if (!xb_suspends || !thread_held()) break;
            LeaveCriticalSection(c);
            xb_thread_safe_point();
        }
        return 1;
    }
    case 304:     /* RtlLeaveCriticalSection(CRITICAL_SECTION *): the same pointer as the Enter before it (first stop: 824b08c0) */
        LeaveCriticalSection(cs_of((u32)a->r[0]));
        return 1;
    case 404:     /* XexCheckExecutablePrivilege(n): first stop, 821b9970 (the video mode query), one argument, 10, and a branch on
                   * a zero result. The XEX system flags answer it: bit n of 0x200 */
        *r3 = (xb_xex_system_flags >> ((u32)a->r[0] & 31)) & 1;
        return 1;
    case 303:     /* RtlInitializeCriticalSectionAndSpinCount (name from the Xenia table): first stop 822c2cc8, a CRT function that
                   * calls it with nothing in r3 and sets its own result after; nothing reads an effect */
        return no_effect(imp, r3);
    case 338:     /* KeTlsAlloc(): first stop 822bcd00 (the CRT's thread-data set-up) calls it with nothing, compares the result with -1
                   * and stores it at 824bc2e4 as the TLS index the next import (set value) is passed. The host's own TLS slot stands
                   * for it: a guest index is a host index */
        *r3 = TlsAlloc();
        return 1;
    case 339:     /* KeTlsFree(index): the pair of 338. First stop in Too Human, 82aa6d20 under 82a7a470 on the render thread, after the shader compile, index 0xd */
        *r3 = TlsFree((DWORD)a->r[0]);
        return 1;
    case 189:     /* MmFreePhysicalMemory(type, address): first stop 821b9330 under 8209e6c0, RETURN TO Xbox LIVE Arcade tearing the game down. A block case 186 made
                   * is given back for reuse (xb_free_block) */
        (void)free_block_told((u32)a->r[1]);
        *r3 = 0;
        return 1;
    case 25:      /* ExTerminateThread(status): first stop 821b9230 ending the game's threads on the way out. A guest thread is a host thread */
        ExitThread((DWORD)a->r[0]);
    case 253: {   /* NtWaitForSingleObjectEx(handle, mode, alertable, s64 *timeout): first stop 821ba680 (WaitForSingleObject) under 821af068, on the way out. A negative
                   * timeout is relative, in 100 ns; none means for ever */
        u32 t = (u32)a->r[3];
        DWORD ms = t ? (DWORD)(-(long long)rd64(t) / 10000) : INFINITE;
        if (t && (long long)rd64(t) > 0) xb_stop("import_wait", "a wait until a set time", t);
        if ((u32)a->r[2] && xb_apc_run(a)) { *r3 = STATUS_USER_APC_; return 1; }
        {
            HANDLE w = host_handle((u32)a->r[0]);
            LONG64 t0 = ws_start();
            *r3 = WaitForSingleObject(w, ms);
            if (*r3 == WAIT_OBJECT_0) ws_waited(t0, w, 0);
        }
        return 1;
    }
    case 340:     /* KeTlsGetValue(index): first stop 822bcc90 (the CRT's thread data) on the way out of the sound thread 8209db88. The pair of 341 */
        *r3 = (u32)(ULONG_PTR)TlsGetValue((DWORD)a->r[0]);
        return 1;
    case 341:     /* KeTlsSetValue(index, value): first stop 822bcd00 right after the alloc, r3 the index it stored, r4 a guest address
                   * (20000... the thread-data block it allocated); the result is not read */
        TlsSetValue((DWORD)a->r[0], (LPVOID)(ULONG_PTR)(u32)a->r[1]);
        *r3 = 1;
        return 1;
    case 409: {   /* XexLoadImage(name, flags, min version, HANDLE *out): first stop 821b9ef0, r3 0x8202e534 = "xam.xex", r4 9, r5 0,
                   * r6 a stack slot the caller reads back as the module handle; a negative result takes its error path. A module
                   * of the console's system software is not loaded: the handle is an opaque token and what the game then asks the
                   * module for (XexGetProcedureAddress) is answered by name from the host */
        char name[64];
        u32 p = (u32)a->r[0], n = 0;
        while (n < sizeof name - 1 && (name[n] = (char)rd8(p + n)) != 0) n++;
        name[n] = 0;
        port_log("import XexLoadImage \"%s\" answered with a token, nothing loaded\n", name);
        wr32((u32)a->r[3], 0x7FFE0001u);
        *r3 = 0;
        return 1;
    }
    case 407:     /* XexGetProcedureAddress(module, ordinal, void **out): first stop 821b9eb0, a wrapper that 822babe0 calls six times
                   * (ordinals 0xaff 0xb00 0xb0b 0xb10 0x305 0x30b of the xam module token) and stores the results in a 28-byte
                   * object, freeing it if one is zero. The answer is an address that calls back into the host by library and
                   * ordinal, so the function is named at the stop that calls it */
        if ((u32)a->r[0] == 0x7FFE0001u) {
            u32 f = xb_dyn_import("xam.xex", (unsigned)a->r[1]);
            wr32((u32)a->r[2], f);
            *r3 = f ? 0 : 0xC0000225u;           /* STATUS_NOT_FOUND */
            return 1;
        }
        return 0;
    case 186:     /* the physical-memory allocator (MmAllocatePhysicalMemoryEx): first stop 821b92a0 under the XDK's d3d allocation
                   * chain (821c6c50, 821d9358), r3 0, r4 0x12c0 (the size), r5 0x404 (read/write, write-combined). The result is the
                   * guest address of the block. Guest physical memory is ordinary guest memory here; a block the GPU reads is copied
                   * by the d3d backend at the call that uses it */
    {   /* (flags, size, protect, min, max, alignment): 4 KB pages, 64 KB with MEM_LARGE_PAGES (0x20000000) in protect, and at least the alignment asked for. Given
         * back by MmFreePhysicalMemory (case 189) */
        u32 align = (u32)a->r[2] & 0x20000000u ? 0x10000u : 0x1000u, want = (u32)a->r[5];
        if (want && !(want & (want - 1)) && want <= 0x1000000u && want > align) align = want;
        *r3 = xb_alloc_block((u32)a->r[1], align);
        return 1;
    }
    case 190:     /* MmGetPhysicalAddress(address): first stop 82a06228 (XMA set-up) under 82da3600 on Too Human's thread 82d42b78, r3 a guest block.
                   * Guest memory starts at 0x20000000 and the GPU's addresses are that less the base (fetch constants read back as phys | 0x20000000) */
        *r3 = (u32)a->r[0] & 0x1fffffffu;
        return 1;
    case 548:     /* XMACreateContext(u32 *context out): first stop 82a06534 (the XMA driver's voice set-up, each voice's word +0x40) under 82da3600 on
                   * Too Human's thread 82d42b78. The registers are plain memory (xb_game.h); xb_xma_hw.c's silent contexts stand behind them */
#ifdef XB_XMA_WINDOW
        *r3 = xb_xma_create((u32)a->r[0]);
#else
        *r3 = 0x8007000Eu;   /* E_OUTOFMEMORY, the kernel's answer with no context free: no XMA hardware */
#endif
        return 1;
    case 550:     /* XMAReleaseContext(context): the context XMACreateContext gave */
#ifdef XB_XMA_WINDOW
        xb_xma_release((u32)a->r[0]);
#endif
        *r3 = 0;
        return 1;
    case 132: {   /* KeQuerySystemTime(u64 *out): first stop 821b9130, r3 an 8-byte stack slot the caller reads back with ld (CRT seed) */
        FILETIME ft;
        GetSystemTimeAsFileTime(&ft);
        wr64((u32)a->r[0], ((u64)ft.dwHighDateTime << 32) | ft.dwLowDateTime);
        return 1;
    }
    case 13: {    /* ExCreateThread(HANDLE *out, stack size, u32 *id out, xapi start-up, start, context, flags): first stop 82329534, r3 a stack
                   * slot, r4 0, r5 0, r6 821bb250. The start-up is the game's own wrapper: it takes the start and the context in r3 and r4 and
                   * calls the start. Flag bit 0 is created suspended (the kernel's X_CREATE_SUSPENDED); the top byte is a processor mask: the
                   * host places the thread, the block says that processor */
        u32 stack = (u32)a->r[1] < 0x40000u ? 0x40000u : (u32)a->r[1], pcr;
        u32 xapi = (u32)a->r[3], start = (u32)a->r[4], ctx = (u32)a->r[5], flags = (u32)a->r[6];
        XbThread *t = (XbThread *)malloc(sizeof *t);
        DWORD id;
        HANDLE h;
        t->entry = xapi ? xapi : start;
        t->r3 = xapi ? start : ctx;
        t->r4 = xapi ? ctx : 0;
        t->sp = xb_alloc(stack) + stack - 0x100u;
        h = CreateThread(NULL, 16u << 20, guest_thread, t, CREATE_SUSPENDED | STACK_SIZE_PARAM_IS_A_RESERVATION, &id);
        if (!h) xb_stop("import_failed", "CreateThread", GetLastError());
        pcr = new_processor_block(id);
        t->pcr = pcr;
        set_processor(pcr, flags >> 24);
        if ((u32)a->r[0]) {
            u32 g = handle_new(h);
            wr32((u32)a->r[0], g);
            if (s_thread_pcr_n < 64) s_thread_pcr[s_thread_pcr_n].handle = g, s_thread_pcr[s_thread_pcr_n++].pcr = pcr;
        }
        if ((u32)a->r[2]) wr32((u32)a->r[2], id);
        port_log("import ExCreateThread start %08x context %08x flags %x stack %x: guest thread %u\n", start, ctx, flags, stack, id);
        if (!(flags & 1u)) ResumeThread(h);
        *r3 = 0;
        return 1;
    }
    case 272:     /* ObReferenceObjectByHandle(handle, type, void **out): first stop 821b9618 (set thread processor) with the handle ExCreateThread
                   * gave and the kernel's thread type, then the object goes to KeSetAffinityThread and ObDereferenceObject only: the object
                   * the game holds is the handle's token */
        wr32((u32)a->r[2], (u32)a->r[0]);
        *r3 = 0;
        return 1;
    case 151:     /* KeSetAffinityThread(object, mask, u32 *old): 821b9618, right after the reference; the caller turns the old mask back into a
                   * processor number. The host places the threads: the mask only changes the processor number in the thread's block */
    {
        u32 pcr = thread_pcr((u32)a->r[0]);
        wr32((u32)a->r[2], 1u << (pcr ? rd8(pcr + 0x10cu) : 0));
        if (pcr) set_processor(pcr, (u32)a->r[1]);
        *r3 = 0;
        return 1;
    }
    case 207: {   /* NtClose(handle), one that is not a file (xb_file.c takes those): first stop 821b7cc0 under 820919c0, an empty content list's token */
        HANDLE h;
        if (!(u32)a->r[0]) {   /* 82090cc8 closes its save file before opening it, and on an existing save the word is still zero: the kernel's invalid handle, ignored */
            *r3 = XB_STATUS(0xC0000008u);
            return 1;
        }
        h = host_handle((u32)a->r[0]);
        xb_content_token_closed((u32)a->r[0]);
        if (h) CloseHandle(h);
        *r3 = 0;
        return 1;
    }
    case 261:     /* ObDereferenceObject(object): the pair of 272; a token holds no reference */
    case 153:     /* KeSetBasePriorityThread(object, priority): 821b9500, the same shape. The host schedules; the result is not used */
    case 133:     /* KeRaiseIrqlToDpcLevel(): first stop 82a10228 under the XMA driver, 82a0f2c8. It masks only this processor's DPCs, and the port has none; other processors never
                   * waited on it, so it protects nothing the host threads need. Old level 0 */
    case 179:     /* KfLowerIrql(old): the pair of 133 */
        return no_effect(imp, r3);
    case 93:      /* KeEnableFpuExceptions(enable): first stop in Too Human, 82a6c9f8 under 82aa1b18 on the render thread, enable 0. Translated code raises no guest FPU
                   * exceptions, so turning them off changes nothing; the old setting is off too, 0. Turning them on would, so it stops */
        if ((u32)a->r[0]) return 0;
        return no_effect(imp, r3);
    case 77:      /* KeAcquireSpinLockAtRaisedIrql(lock): first stop 82a10228 under the XMA driver, r3 8337e878, right after 133. A real lock between processors, so a real one here, on the
                   * guest word. A host thread holding it can be preempted, which a raised console one cannot: the waiter yields */
    case 177:     /* KfAcquireSpinLock(lock): the same, answering the old level */
        while (InterlockedCompareExchange((volatile LONG *)(tc_base + (u32)a->r[0]), 1, 0)) SwitchToThread();
        *r3 = 0;
        return 1;
    case 174:     /* KeTryToAcquireSpinLockAtRaisedIrql(lock): 1 if taken */
        *r3 = InterlockedCompareExchange((volatile LONG *)(tc_base + (u32)a->r[0]), 1, 0) == 0;
        return 1;
    case 137:     /* KeReleaseSpinLockFromRaisedIrql(lock): the pair of 77 */
    case 180:     /* KfReleaseSpinLock(lock, old): the pair of 177 */
        InterlockedExchange((volatile LONG *)(tc_base + (u32)a->r[0]), 0);
        *r3 = 0;
        return 1;
    case 245: {   /* NtResumeThread(handle, u32 *previous): 821b94c0, starts a thread ExCreateThread made suspended, or one NtSuspendThread stopped */
        HANDLE h = host_handle((u32)a->r[0]);
        u32 prev;
        if (!thread_resume(h, &prev)) prev = ResumeThread(h);
        if ((u32)a->r[1]) wr32((u32)a->r[1], prev);
        *r3 = 0;
        return 1;
    }
    case 252: {   /* NtSuspendThread(handle, u32 *previous): first stop 82a48648 (SuspendThread) under 829baaf8 on the main thread at frame 104, r3 7ffe2034 */
        HANDLE h = host_handle((u32)a->r[0]);
        static int said;
        u32 prev;
        if (said++ < 8) port_log("import NtSuspendThread %08x (host thread %lu) from host thread %lu\n", (u32)a->r[0], GetThreadId(h), GetCurrentThreadId());
        prev = thread_suspend(h);
        if ((u32)a->r[1]) wr32((u32)a->r[1], prev);
        *r3 = 0;
        return 1;
    }
    case 146: {   /* KeResumeThread(object): first stop 82a04330 under the XMA driver's start 82a03a30, r3 7ffe2084, the token 272 gave for a thread made suspended. Answers the previous count */
        HANDLE h = host_handle((u32)a->r[0]);
        u32 prev;
        if (!thread_resume(h, &prev)) prev = ResumeThread(h);
        *r3 = prev;
        return 1;
    }
    case 176: {   /* KeWaitForSingleObject(object, reason, mode, alertable, s64 *timeout): first stop 8227fbc0 (thunk 82329844) on the first guest thread,
                   * r3 2001abf4 inside a game object, r4 3, r5 1, r6 0. The object is an event the game set up in place */
        if ((u32)a->r[3] && xb_apc_run(a)) { *r3 = STATUS_USER_APC_; return 1; }
        *r3 = wait_host(kevent_of((u32)a->r[0]), (u32)a->r[4]);
        return 1;
    }
    case 175: {   /* KeWaitForMultipleObjects(count, object pointers, wait type, reason, mode, alertable, s64 *timeout, wait blocks): first stop 82329bb4
                   * on the audio worker thread (8227f8b8), r3 3, r4 2035f700 an array of three event addresses, r5 1 (any). The result is the index
                   * of the event that ended the wait, as WaitForMultipleObjects gives it */
        HANDLE h[64];
        u32 i, n = (u32)a->r[0], tp = (u32)a->r[6];
        DWORD ms = INFINITE, got;
        if (n > 64) xb_stop("wait_object", "a wait on more than 64 objects", n);
        if ((u32)a->r[5] && xb_apc_run(a)) { *r3 = STATUS_USER_APC_; return 1; }
        for (i = 0; i < n; i++) h[i] = kevent_of(rd32((u32)a->r[1] + 4 * i));
        if (tp) { s64 t = (s64)rd64(tp); ms = t >= 0 ? 0 : (DWORD)((-t + 9999) / 10000); }
        {
            LONG64 t0 = ws_start();
            got = WaitForMultipleObjects(n, h, (u32)a->r[2] == 0, ms);
            if (got < WAIT_OBJECT_0 + n) ws_waited(t0, h[got - WAIT_OBJECT_0], 0);
        }
        *r3 = got == WAIT_TIMEOUT ? 0x102u : got - WAIT_OBJECT_0;
        return 1;
    }
    case 511:     /* XAudioGetSpeakerConfig(u32 *config): first stop 82282ca8 under the audio library's engine start (8229cab0), r3 a stack word. The value
                   * is the kernel's packed record for a stereo output; the game mixes what it says and the host mixes that down */
        wr32((u32)a->r[0], 0x00010001u);
        port_log("import XAudioGetSpeakerConfig answered stereo\n");
        *r3 = 0;
        return 1;
    case 499:     /* XAudioRegisterRenderDriverClient(struct { routine, context } *, u32 *driver out): first stop 82282ca8, r3 a stack block, r4 2001f5d0
                   * in the audio engine object. The console then calls the routine once for each frame of output it wants */
        s_audio_routine = rd32((u32)a->r[0]);
        s_audio_context = rd32((u32)a->r[0] + 4);
        wr32((u32)a->r[1], AUDIO_DRIVER);
        port_log("import XAudioRegisterRenderDriverClient routine %08x context %08x\n", s_audio_routine, s_audio_context);
        CreateThread(NULL, 16u << 20, audio_clock, NULL, STACK_SIZE_PARAM_IS_A_RESERVATION, NULL);
        *r3 = 0;
        return 1;
    case 215: {   /* NtCreateTimer(HANDLE *out, attributes, type): first stop 82ec6840 (the XDK's CreateWaitableTimer, type 1 when its manual-reset argument is 0) on the thread 82d1fdb8, r3 a stack word, r5 1 */
        HANDLE h = CreateWaitableTimerA(NULL, (u32)a->r[2] == 0, NULL);
        if (!h) xb_stop("import_failed", "CreateWaitableTimer", GetLastError());
        wr32((u32)a->r[0], handle_new(h));
        *r3 = 0;
        return 1;
    }
    case 250: {   /* NtSetTimerEx(handle, s64 *due, routine, mode, context, resume, period ms, previous): first stop 82ec67d0 (the XDK's SetWaitableTimer) on the thread 82d1fdb8, r3 215's handle,
                   * no routine, period 10. Due as on the host, negative for relative. A timer with a routine stops: nothing has needed its APC */
        LARGE_INTEGER due;
        if ((u32)a->r[2]) return 0;
        due.QuadPart = (LONGLONG)rd64((u32)a->r[1]);
        *r3 = SetWaitableTimer(host_handle((u32)a->r[0]), &due, (LONG)(u32)a->r[6], NULL, NULL, FALSE) ? 0u : XB_STATUS(0xC0000001u);
        return 1;
    }
    case 503:     /* XAudioGetVoiceCategoryVolumeChangeMask(driver, u32 *mask): first stop 82a049f0 on the XMA driver's thread 82a041b8, r3 the driver 499 gave. No category's volume has changed */
        wr32((u32)a->r[1], 0);
        *r3 = 0;
        return 1;
    case 501:     /* XAudioSubmitRenderDriverFrame(driver, samples): the routine's answer: 256 big-endian floats for each of 6 channels, one channel after
                   * the other */
        audio_frame((u32)a->r[1]);
        *r3 = 0;
        return 1;
    case 16:      /* ExGetXConfigSetting(category, setting, buffer, size, u16 *size out): first stop 821b8f78 under 82097988, r3 3, r4 9 (the user category's
                   * language), r5 a stack word, r6 4. English, the first language; another setting stops so that each one is answered on purpose */
        if ((u32)a->r[0] == 3 && (u32)a->r[1] == 9 && (u32)a->r[3] >= 4) {
            wr32((u32)a->r[2], 1);
            if ((u32)a->r[4]) wr16((u32)a->r[4], 4);
            port_log("import ExGetXConfigSetting language answered English\n");
            *r3 = 0;
            return 1;
        }
        if ((u32)a->r[0] == 3 && (u32)a->r[1] == 14 && (u32)a->r[3] >= 1) {   /* the country, one byte (821b8d78 maps it to a locale): the United States */
            wr8((u32)a->r[2], 103);
            if ((u32)a->r[4]) wr16((u32)a->r[4], 1);
            *r3 = 0;
            return 1;
        }
        return 0;
    case 131:     /* KeQueryPerformanceFrequency(): first stop 821b8a48 under 82092c20; the rate of the time base the game reads (tc_timebase, xb_mem.c) */
        *r3 = 49875000u;
        return 1;
    case 300: {   /* RtlInitAnsiString(struct { u16 length, u16 room, u32 text } *, text): first stop 821b9da8 under 821b7bf8 (82090000), r3 a stack block */
        u32 d = (u32)a->r[0], t = (u32)a->r[1], n = 0;
        if (t) while (rd8(t + n)) n++;
        wr16(d, (u16)n);
        wr16(d + 2, (u16)(t ? n + 1 : 0));
        wr32(d + 4, t);
        return 1;
    }
    case 220:     /* NtFreeVirtualMemory(u32 *base, u32 *size, type, 0): first stop 821bc828, the C library's heap giving back pages it had committed (0x4000, decommit). The
                   * range stays committed here and reads as zero, which is how the heap finds it when it commits it again. A whole-region release keeps its addresses:
                   * xb_alloc does not reuse them, but a release (0x8000) of a region case 204 made is given back */
        if ((u32)a->r[2] == 0x4000u) memset(tc_base + rd32((u32)a->r[0]), 0, rd32((u32)a->r[1]));
        if ((u32)a->r[2] & 0x8000u) {   /* MEM_RELEASE of a region NtAllocateVirtualMemory made: given back for reuse (xb_free_block), its size written back */
            u32 n = free_block_told(rd32((u32)a->r[0]));
            if (n) wr32((u32)a->r[1], n);
        }
        *r3 = 0;
        return 1;
    case 196:     /* MmQueryAddressProtect(address): first stop 821be648, which tests the 0x600 bits (uncached, write combined) to pick how to copy into the buffer.
                   * All of the kit's memory is plain read and write */
        *r3 = 4;
        return 1;
    case 90: {    /* KeDelayExecutionThread(mode, alertable, s64 *time): first stop 821ba128 (Sleep) under 8209b848. The time as in wait_host; 0 gives up the rest of the slice */
        s64 t = (s64)rd64((u32)a->r[2]);
        if ((u32)a->r[1] && xb_apc_run(a)) { *r3 = STATUS_USER_APC_; return 1; }
        Sleep(t >= 0 ? 0 : (DWORD)((-t + 9999) / 10000)); /* not the tick: the game's own Sleep call */
        *r3 = 0;
        return 1;
    }
    case 309:     /* RtlNtStatusToDosError(status): first stop 821b9d50 (SetLastError's helper) under 821b8c90, the end of a directory. Each status is added when
                   * a stop passes it */
        switch ((u32)a->r[0]) {
        case 0x80000006u: *r3 = 18; return 1;    /* ERROR_NO_MORE_FILES */
        case 0xC0000034u: *r3 = 2; return 1;     /* ERROR_FILE_NOT_FOUND */
        case 0xC0000008u: *r3 = 6; return 1;     /* ERROR_INVALID_HANDLE */
        case 0xC0000011u: *r3 = 38; return 1;    /* ERROR_HANDLE_EOF */
        }
        {   /* the console's table is NT's: any other status goes to the host's own. First met in Too Human: c0000002 from the library's
             * not-implemented stub 82ec65d8 on the job thread */
            static ULONG (WINAPI *nt_to_dos)(LONG);
            if (!nt_to_dos) nt_to_dos = (ULONG (WINAPI *)(LONG))(void *)GetProcAddress(GetModuleHandleA("ntdll.dll"), "RtlNtStatusToDosError");
            if (!nt_to_dos) return 0;
            *r3 = nt_to_dos((LONG)(u32)a->r[0]);
            return 1;
        }
    case 209: {   /* NtCreateEvent(HANDLE *out, attributes, type, initial): first stop 821b7bf8 (the CreateEvent wrapper): it turns its manual-reset argument
                   * into type 0 manual, 1 automatic, and reads the handle back on a result that is not negative */
        HANDLE h = CreateEventA(NULL, (u32)a->r[2] == 0, (u32)a->r[3] != 0, NULL);
        wr32((u32)a->r[0], handle_new(h));
        *r3 = 0;
        return 1;
    }
    case 246:     /* NtSetEvent(handle, u32 *previous): first stop 821b1458 under 821b3538, a partner's session closing when the host has gone. It wakes the game's worker
                   * thread 821b1328, which doesn't ask for the previous state. From the vertical blank interrupt, see handle_given */
        if (!handle_given((u32)a->r[0])) {
            *r3 = 0xC0000008u;
            return 1;
        }
        WS_SIGNAL(host_handle((u32)a->r[0]));
        SetEvent(host_handle((u32)a->r[0]));
        *r3 = 0;
        return 1;
    case 157:     /* KeSetEvent(event, increment, wait): the event of 176 */
        WS_SIGNAL(kevent_of((u32)a->r[0]));
        SetEvent(kevent_of((u32)a->r[0]));
        *r3 = 0;
        return 1;
    case 143:     /* KeResetEvent(event): the event of 176 */
        ResetEvent(kevent_of((u32)a->r[0]));
        *r3 = 0;
        return 1;
    case 21:      /* ExRegisterTitleTerminateNotification(struct *, register): first stop at thunk 823296d4, r3 824bb568 (a static: the routine to
                   * call and a priority), r4 1 (add). The console calls the routine when the title is closed from the dashboard; the
                   * port's window closing ends the process, so the routine is not kept and nothing reads a result */
        return no_effect(imp, r3);
    case 95:      /* KeEnterCriticalRegion() and */
    case 125:     /* KeLeaveCriticalRegion(): no arguments (first stop: 823297c4 under the D3D or audio library's lock, r3 an object of the
                   * game's heap). They only hold off the kernel's asynchronous procedure calls on the thread; the port delivers none, so
                   * nothing changes and the game reads no result */
        return no_effect(imp, r3);
    case 323: {   /* RtlUnicodeToMultiByteN(char *out, room, u32 *written, const u16 *in, bytes): first stop in Too Human, 82a45c30 under 82409058. The code page is Latin-1 */
        u32 o = (u32)a->r[0], room = (u32)a->r[1], w = (u32)a->r[2], in = (u32)a->r[3], n = (u32)a->r[4] / 2, i;
        if (n > room) n = room;
        for (i = 0; i < n; i++) {
            u16 c = rd16(in + i * 2);
            wr8(o + i, c < 256 ? (u8)c : '?');
        }
        if (w) wr32(w, n);
        *r3 = 0;
        return 1;
    }
    case 307: {   /* RtlMultiByteToUnicodeN(u16 *out, room in bytes, u32 *written, const char *in, bytes): the pair of 323 */
        u32 o = (u32)a->r[0], room = (u32)a->r[1] / 2, w = (u32)a->r[2], in = (u32)a->r[3], n = (u32)a->r[4], i;
        if (n > room) n = room;
        for (i = 0; i < n; i++) wr16(o + i * 2, rd8(in + i));
        if (w) wr32(w, n * 2);
        *r3 = 0;
        return 1;
    }
    case 301: {   /* RtlInitUnicodeString(struct { u16 length, u16 room, u32 text } *, text): as 300, in bytes */
        u32 d = (u32)a->r[0], t = (u32)a->r[1], n = 0;
        if (t) while (rd16(t + n * 2)) n++;
        wr16(d, (u16)(n * 2));
        wr16(d + 2, (u16)(t ? n * 2 + 2 : 0));
        wr32(d + 4, t);
        return 1;
    }
    case 405:     /* XexGetModuleHandle(name, HANDLE *out): first stop in Too Human, 82a6eeb0 under 82770830, name 0, the title itself. A handle only needs to be its own: the image's address */
        if ((u32)a->r[0] != 0) xb_stop("import_failed", "XexGetModuleHandle of another module", (u32)a->r[0]);
        wr32((u32)a->r[1], 0x82000000u);
        *r3 = 0;
        return 1;
    case 33:      /* FscSetCacheElementCount(0, count): first stop in Too Human, 82a45fd8 at the top of main (8243c990), 0xc0. The size of the kernel's disc cache, which the port doesn't have */
        return no_effect(imp, r3);
    case 102:     /* KeGetCurrentProcessType(): no arguments, 52 call sites in 46 functions compare the result with 1 (first stop:
                   * 821bc3d8 and 821bcb90, the heap). A title is a user process: 1 */
        *r3 = 1;
        return 1;
    }
    return 0;
}
