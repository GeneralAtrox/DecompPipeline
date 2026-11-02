/* xb_imports.c, the kernel's imports (one translation unit with it, for its helpers; here for the 400-line cap) */
static int krnl_call(const XbImp *imp, const TcArgs *a, u64 *r3, f64 *f1)
{
    (void)f1;
    (void)imp;
    switch (imp->ord) {
    case 204: {   /* NtAllocateVirtualMemory(u32 *base, u32 *size, type, protect): first stop, 822bb160's caller: r3, r4 stack
                   * addresses, r5 0x60002000, r6 4 (PAGE_READWRITE) */
        u32 pb = (u32)a->r[0], ps = (u32)a->r[1], want = rd32(pb), size = rd32(ps);
        if (!want) want = xb_alloc(size);        /* a request for a given base is already in the reserve: the game keeps it */
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
        EnterCriticalSection(cs_of((u32)a->r[0]));
        return 1;
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
    case 189:     /* MmFreePhysicalMemory(type, address): first stop 821b9330 under 8209e6c0, RETURN TO Xbox LIVE Arcade tearing the game down. Nothing is given back:
                   * xb_alloc does not reuse */
        *r3 = 0;
        return 1;
    case 25:      /* ExTerminateThread(status): first stop 821b9230 ending the game's threads on the way out. A guest thread is a host thread */
        ExitThread((DWORD)a->r[0]);
    case 253: {   /* NtWaitForSingleObjectEx(handle, mode, alertable, s64 *timeout): first stop 821ba680 (WaitForSingleObject) under 821af068, on the way out. A negative
                   * timeout is relative, in 100 ns; none means for ever */
        u32 t = (u32)a->r[3];
        DWORD ms = t ? (DWORD)(-(long long)rd64(t) / 10000) : INFINITE;
        if (t && (long long)rd64(t) > 0) xb_stop("import_wait", "a wait until a set time", t);
        *r3 = WaitForSingleObject(host_handle((u32)a->r[0]), ms);
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
        *r3 = xb_alloc((u32)a->r[1]);
        return 1;
    case 132: {   /* KeQuerySystemTime(u64 *out): first stop 821b9130, r3 an 8-byte stack slot the caller reads back with ld (CRT seed) */
        FILETIME ft;
        GetSystemTimeAsFileTime(&ft);
        wr64((u32)a->r[0], ((u64)ft.dwHighDateTime << 32) | ft.dwLowDateTime);
        return 1;
    }
    case 13: {    /* ExCreateThread(HANDLE *out, stack size, u32 *id out, xapi start-up, start, context, flags): first stop 82329534, r3 a stack
                   * slot, r4 0, r5 0, r6 821bb250. The start-up is the game's own wrapper: it takes the start and the context in r3 and r4 and
                   * calls the start. Flag bit 0 is created suspended (the kernel's X_CREATE_SUSPENDED); the upper bits pick a processor, which
                   * the port leaves to the host */
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
        if ((u32)a->r[0]) wr32((u32)a->r[0], handle_new(h));
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
                   * processor number. The host places the threads, so the mask is not applied and the old one reads processor 0 */
        wr32((u32)a->r[2], 1u);
        *r3 = 0;
        return 1;
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
        return no_effect(imp, r3);
    case 245:     /* NtResumeThread(handle, u32 *previous): 821b94c0, starts a thread ExCreateThread made suspended */
        if ((u32)a->r[1]) wr32((u32)a->r[1], ResumeThread(host_handle((u32)a->r[0])));
        else ResumeThread(host_handle((u32)a->r[0]));
        *r3 = 0;
        return 1;
    case 176: {   /* KeWaitForSingleObject(object, reason, mode, alertable, s64 *timeout): first stop 8227fbc0 (thunk 82329844) on the first guest thread,
                   * r3 2001abf4 inside a game object, r4 3, r5 1, r6 0. The object is an event the game set up in place */
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
        for (i = 0; i < n; i++) h[i] = kevent_of(rd32((u32)a->r[1] + 4 * i));
        if (tp) { s64 t = (s64)rd64(tp); ms = t >= 0 ? 0 : (DWORD)((-t + 9999) / 10000); }
        got = WaitForMultipleObjects(n, h, (u32)a->r[2] == 0, ms);
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
                   * xb_alloc does not reuse them */
        if ((u32)a->r[2] == 0x4000u) memset(tc_base + rd32((u32)a->r[0]), 0, rd32((u32)a->r[1]));
        *r3 = 0;
        return 1;
    case 196:     /* MmQueryAddressProtect(address): first stop 821be648, which tests the 0x600 bits (uncached, write combined) to pick how to copy into the buffer.
                   * All of the kit's memory is plain read and write */
        *r3 = 4;
        return 1;
    case 90: {    /* KeDelayExecutionThread(mode, alertable, s64 *time): first stop 821ba128 (Sleep) under 8209b848. The time as in wait_host; 0 gives up the rest of the slice */
        s64 t = (s64)rd64((u32)a->r[2]);
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
        return 0;
    case 209: {   /* NtCreateEvent(HANDLE *out, attributes, type, initial): first stop 821b7bf8 (the CreateEvent wrapper): it turns its manual-reset argument
                   * into type 0 manual, 1 automatic, and reads the handle back on a result that is not negative */
        HANDLE h = CreateEventA(NULL, (u32)a->r[2] == 0, (u32)a->r[3] != 0, NULL);
        wr32((u32)a->r[0], handle_new(h));
        *r3 = 0;
        return 1;
    }
    case 246:     /* NtSetEvent(handle, u32 *previous): first stop 821b1458 under 821b3538, a partner's session closing when the host has gone. It wakes the game's worker
                   * thread 821b1328, which doesn't ask for the previous state */
        SetEvent(host_handle((u32)a->r[0]));
        *r3 = 0;
        return 1;
    case 157:     /* KeSetEvent(event, increment, wait): the event of 176 */
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
    case 102:     /* KeGetCurrentProcessType(): no arguments, 52 call sites in 46 functions compare the result with 1 (first stop:
                   * 821bc3d8 and 821bcb90, the heap). A title is a user process: 1 */
        *r3 = 1;
        return 1;
    }
    return 0;
}
