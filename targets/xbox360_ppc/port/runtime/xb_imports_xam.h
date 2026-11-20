/* xb_imports.c, the imports of XAM, the console's system library (one translation unit with it, for its helpers; here for the 400-line cap) */
/* With no network: the error the NetDll answers below leave for WSAGetLastError, and a once-per-ordinal log (ordinals below 32) */
static volatile u32 s_nonet_error;
static int ord_once(u32 *said, u32 ord) { u32 bit = 1u << (ord & 31u); if (*said & bit) return 0; *said |= bit; return 1; }
/* System notifications. Each listener keeps its own queue of those posted in its areas (a bit per area, id >> 25), as the console gives every
 * listener its own copy: one shared queue let whichever of Too Human's six listeners read first take a note meant for another. A listener's host
 * event stays set while its queue holds something. A game waits on one with its own events (Too Human: 82ec66d8 on the thread 82d42b78, the
 * listener of mask 0x20 and two events) */
#define NOTE_Q 16u
static struct { u32 token, mask, q[NOTE_Q][2]; unsigned rd, wr; } s_listen[8];
static unsigned s_listen_n;
static int s_note_startup;
static SRWLOCK s_note_lock = SRWLOCK_INIT;

static void note_to(unsigned k, u32 id, u32 param)   /* under s_note_lock */
{
    if (s_listen[k].wr - s_listen[k].rd >= NOTE_Q) { port_log("notification %08x (%08x) dropped: listener %u's queue is full\n", id, param, k); return; }
    s_listen[k].q[s_listen[k].wr % NOTE_Q][0] = id;
    s_listen[k].q[s_listen[k].wr % NOTE_Q][1] = param;
    s_listen[k].wr++;
    WS_SIGNAL(host_handle(s_listen[k].token));
    SetEvent(host_handle(s_listen[k].token));
}

static u32 listener_new(u32 mask)
{
    u32 t = handle_new(CreateEventA(NULL, TRUE, FALSE, NULL));
    unsigned k;
    AcquireSRWLockExclusive(&s_note_lock);
    if (s_listen_n >= 8) xb_stop("out_of_memory", "notification listeners", s_listen_n);
    k = s_listen_n++;
    s_listen[k].token = t, s_listen[k].mask = mask, s_listen[k].rd = s_listen[k].wr = 0;
    /* The console's start-up notes, to the first listener of the system area (mask bit 0): the system interface shown and gone (XN_SYS_UI, id 9,
     * 1 then 0), and the sign-in state twice (XN_SYS_SIGNINCHANGED, id 0xA, the signed-in users' mask: user 0's local profile, as 528 and 551
     * answer). Too Human never asks 528 or 551 in its front end; it learns the sign-in from these, and without them its main menu's Create
     * Character and Online Multiplayer answer "CANNOT SAVE: not signed-in" */
    if (!s_note_startup && (mask & 1u)) {
        s_note_startup = 1;
        note_to(k, 9, 1), note_to(k, 9, 0), note_to(k, 0xA, 1), note_to(k, 0xA, 1);
        port_log("import xam#650 listener %u (mask %08x): the start-up notes posted (system interface 1, 0; sign-in mask 1, twice)\n", k, mask);
    }
    ReleaseSRWLockExclusive(&s_note_lock);
    return t;
}

static void note_post(u32 id, u32 param)
{
    unsigned k;
    AcquireSRWLockExclusive(&s_note_lock);
    for (k = 0; k < s_listen_n; k++)
        if (s_listen[k].mask & (1u << (id >> 25))) note_to(k, id, param);
    ReleaseSRWLockExclusive(&s_note_lock);
}

/* XNotifyGetNext's body: the oldest note in the listener's queue (the oldest of id `want` when it is not 0, the others kept in order); 0 when
 * there is none. The listener's event is reset once its queue is empty */
static int note_next(u32 token, u32 want, u32 *id, u32 *param)
{
    unsigned k, i, j;
    int got = 0;
    AcquireSRWLockExclusive(&s_note_lock);
    for (k = 0; k < s_listen_n && s_listen[k].token != token; k++) {}
    if (k < s_listen_n) {
        for (i = s_listen[k].rd; i != s_listen[k].wr && want && s_listen[k].q[i % NOTE_Q][0] != want; i++) {}
        if (i != s_listen[k].wr) {
            *id = s_listen[k].q[i % NOTE_Q][0], *param = s_listen[k].q[i % NOTE_Q][1];
            for (j = i; j != s_listen[k].rd; j--) s_listen[k].q[j % NOTE_Q][0] = s_listen[k].q[(j - 1) % NOTE_Q][0], s_listen[k].q[j % NOTE_Q][1] = s_listen[k].q[(j - 1) % NOTE_Q][1];
            s_listen[k].rd++;
            got = 1;
        }
        if (s_listen[k].rd == s_listen[k].wr) ResetEvent(host_handle(token));
    }
    ReleaseSRWLockExclusive(&s_note_lock);
    return got;
}
static int xam_call(const XbImp *imp, const TcArgs *a, u64 *r3, f64 *f1)
{
    (void)f1;
    (void)imp;
    switch (imp->ord) {
    case 3:       /* NetDll_socket(1, family, type, protocol): first stop in Too Human, 82d17800 under 8276a398, a VDP socket for a thread it never checks. With no network every socket call fails. The set: bind, ioctlsocket, closesocket follow it */
        s_nonet_error = 0x2742u;   /* WSAENETDOWN */
        *r3 = XB_STATUS(0xFFFFFFFFu);
        return 1;
    case 4: case 5: case 6: case 7: case 8: case 9: case 10: case 11: case 12: case 13: case 14:
    case 18: case 19: case 20: case 21: case 22: case 23: case 24: case 25: {
                  /* closesocket, shutdown, ioctlsocket, setsockopt, getsockopt, getsockname, getpeername, bind, connect, listen, accept, recv, WSARecv,
                   * recvfrom, WSARecvFrom, send, WSASend, sendto, WSASendTo (1, socket, ...): socket 3 gave none, so each is SOCKET_ERROR with
                   * WSAENOTSOCK, as the console answers an invalid socket. Too Human: recvfrom (#20) on socket ffffffff from its network thread
                   * once user 0 is signed in (r6 0x4fd bytes), its first stop after the sign-in notes */
        static u32 said;
        if (ord_once(&said, imp->ord)) port_log("import xam#%u NetDll on socket %08x: no network, so no socket: SOCKET_ERROR, WSAENOTSOCK\n", imp->ord, (u32)a->r[1]);
        s_nonet_error = 0x2736u;   /* WSAENOTSOCK */
        *r3 = XB_STATUS(0xFFFFFFFFu);
        return 1;
    }
    case 27:      /* WSAGetLastError() with no network: the last of the answers above, on any thread (the game's network thread is the only caller) */
        *r3 = s_nonet_error;
        return 1;
    case 28:      /* WSASetLastError(error) */
        s_nonet_error = (u32)a->r[0];
        *r3 = 0;
        return 1;
    case 73:      /* NetDll_XNetGetTitleXnAddr(1, XNADDR *): first stop in Too Human, 82d17738 under 8276a398. An empty address and XNET_GET_XNADDR_NONE: no network */
        memset((u8 *)tc_base + (u32)a->r[1], 0, 36);
        *r3 = 1;
        return 1;
    case 64:      /* NetDll_XNetXnAddrToMachineId(1, XNADDR *, u64 *id): first stop in Too Human, 82d17698 under 8276a398, the empty address of case 73. Id 0 and WSAEINVAL */
        wr64((u32)a->r[2], 0);
        *r3 = 0x2726;
        return 1;
    case 537:     /* XamUserReadProfileSettings(title, user, xuid count, xuids, count, ids, u32 *size, buffer, overlapped): first stop 82d21c48, r3 fffe07d1 (the dashboard's settings), r4 0xff,
                   * 3 ids, buffer 0: asks the size, and any answer but 0x7a fails that voice engine's creation. 82d20e80 then reads, and on an error keeps its defaults (0x26404):
                   * the port has no profile, so it says so. The size is the header of 8 and 40 for each setting, the kernel's record. Another title's settings stop */
        if ((u32)a->r[0] == 0xFFFE07D1u) {
            if ((u32)a->r[7]) { *r3 = 0x490u; return 1; }
            wr32((u32)a->r[6], 8u + 40u * (u32)a->r[4]);
            *r3 = 0x7Au;
            return 1;
        }
        /* The game's own settings (title 0 is the running title): Too Human's first stop 82d18e98 under 828fc420, pressing START on the title.
         * The one local profile of 528 has never stored any, so every setting comes back as the console gives a fresh profile: source 0 (no
         * value), its id and user, its type and a zero value; the controller vibration default (3, on) is source 1. The record, 40 bytes:
         * source, pad, user index (or xuid) at +8, id at +16, the data (type byte, then an 8-byte value at +8) at +24; the header is the count
         * and a pointer to the records; a string or binary setting's room (the id's size field) follows the records, empty. */
        {
            u32 title = (u32)a->r[0], user = (u32)a->r[1], nx = (u32)a->r[2], xuids = (u32)a->r[3], n = (u32)a->r[4], ids = (u32)a->r[5];
            u32 psize = (u32)a->r[6], buf = (u32)a->r[7], ov = (u32)a->s[0], need = 8, extra, i;
            static int said;
            if (title != 0 || (nx && (nx != 1 || rd32(xuids) != 0xE0000000u || rd32(xuids + 4) != 1u)) || (!nx && user != 0)) return 0;   /* another title or user: stop */
            if (n < 1 || n > 32 || !psize || !ids) { *r3 = 0x57u; return 1; }   /* ERROR_INVALID_PARAMETER, as the console */
            for (i = 0; i < n; i++) {
                u32 id = rd32(ids + 4 * i), type = id >> 28;
                need += 40u + (type == 4 || type == 6 ? (id >> 16) & 0xFFFu : 0);
            }
            if (said < 8) {
                said++;
                port_log("import xam#537 XamUserReadProfileSettings title %08x user %u, %u ids:", title, user, n);
                for (i = 0; i < n; i++) port_log(" %08x", rd32(ids + 4 * i));
                port_log("; buffer %08x of %u, need %u%s\n", buf, rd32(psize), need, buf && rd32(psize) >= need ? ": no stored value (vibration default)" : ": asks the size");
            }
            if (!buf || rd32(psize) < need) { wr32(psize, need); *r3 = 0x7Au; return 1; }   /* ERROR_INSUFFICIENT_BUFFER with the size */
            memset(tc_base + buf, 0, need);
            wr32(buf, n);
            wr32(buf + 4, buf + 8);
            for (i = 0, extra = buf + 8 + 40u * n; i < n; i++) {
                u32 id = rd32(ids + 4 * i), type = id >> 28, s = buf + 8 + 40u * i;
                if (nx) wr32(s + 8, 0xE0000000u), wr32(s + 12, 1u);
                else wr32(s + 8, user);
                wr32(s + 16, id);
                wr8(s + 24, (u8)type);
                if (type == 4 || type == 6) { wr32(s + 32, 0); wr32(s + 36, extra); extra += (id >> 16) & 0xFFFu; }
                if (id == 0x10040003u) { wr32(s, 1); wr32(s + 32, 3); }   /* XPROFILE_OPTION_CONTROLLER_VIBRATION: on, the console's default */
            }
            *r3 = xb_overlapped_finish(ov, 0, 0);
            return 1;
        }
    case 652:     /* XNotifyPositionUI(position): first stop in Too Human, 828230c8. Where the console's pop-ups show: the port has none. No result */
        *r3 = 0;
        return 1;
    case 971:    /* XGetAVPack(): first stop in Too Human, 82a46930 in the start-up. The cable: 6 is HDMI, which is what the port's video mode claims */
        *r3 = 6;
        return 1;
    case 973:     /* XGetLanguage(): the console's language, 1 is English */
        *r3 = 1;
        return 1;
    case 500: {   /* XMsgInProcessCall (an application message: app, message, a three-word parameter block, 0): first stop 82206048, the
                   * only caller, passes 0xfa, 0x7001b and the address of a stack block of its own three arguments; it returns 0
                   * when the result is not negative and an error otherwise. The 0xfa application is the console's media player and
                   * the game is asking for its state: it answers success and leaves the block zero (nothing is playing) */
        port_log("import xam#500 app %x message %x answered 0, nothing playing\n", (u32)a->r[0], (u32)a->r[1]);
        *r3 = 0;
        return 1;
    }
    case 977: {   /* XGetVideoMode (struct *, 0): first stop 82093cf0 (display set-up in the start-up function, the only caller) hands it
                   * the address of a 28-byte-or-more block it then reads as {width, height, ?, wide at +12, hd at +16}: height is
                   * compared with 720, wide and hd branch to a 640x480 default when both are zero. That is the console's video mode
                   * record (the XDK's XGetVideoMode): the port reports the 1280x720 wide HD mode the game's own table lists; what
                   * the game draws at is the port's choice (the d3d backend's back buffer), not this record */
        u32 m = (u32)a->r[0];
        wr32(m + 0, 1280);
        wr32(m + 4, 720);
        wr32(m + 8, 0);
        wr32(m + 12, 1);
        wr32(m + 16, 1);
        wr32(m + 20, 0x42700000u);   /* 60.0f */
        wr32(m + 24, 1);
        port_log("import xam#977 answered 1280x720 wide hd 60 Hz\n");
        *r3 = 0;
        return 1;
    }
    case 972:     /* XGetGameRegion(): first stop 82097988, which stores the result beside the language and the locale at 876513a0. North America, every
                   * console kind: the same answer as the language and the country */
        *r3 = 0x00FFu;
        return 1;
    case 401: {   /* XamInputGetState(user, flags, state *): first stop 821b8aa0, r3 0, r4 1, r5 876589c0 (a static the game polls), the XInput record: packet
                   * number, buttons, two triggers, four sticks. The pad the base sampled at the last frame's end (console_x360.c), the script's in a test run, which reads no real input */
        static LONG packet;
        u32 st = (u32)a->r[2];
        XbPadState p;
        if (!xb_pad_state((u32)a->r[0], &p)) { *r3 = 0x48Fu; return 1; }   /* ERROR_DEVICE_NOT_CONNECTED */
        wr32(st, (u32)InterlockedIncrement(&packet));
        wr16(st + 4, p.buttons);
        wr8(st + 6, p.lt);
        wr8(st + 7, p.rt);
        wr16(st + 8, (u16)p.lx);
        wr16(st + 10, (u16)p.ly);
        wr16(st + 12, (u16)p.rx);
        wr16(st + 14, (u16)p.ry);
        {   /* XB_PADLOG (xb_call.c): each change of player 1's buttons */
            extern int xb_padlog_on(void);
            static u32 logged = ~0u;
            if (!a->r[0] && p.buttons != logged && xb_padlog_on()) {
                logged = p.buttons;
                port_log("padlog: 401 wButtons %04x (vertical blank %u)\n", p.buttons, xb_frame_number());
            }
        }
        *r3 = 0;
        return 1;
    }
    case 402: {   /* XamInputSetState(user, flags, XINPUT_VIBRATION *, ...): Too Human, first stop 8312e3ac under 82a45d00 <- 82a22398, Start Campaign with a
                   * character (build box A mn21a). { u16 left motor, u16 right motor }, held until the next call: the base's rumble for that player (the
                   * stronger motor, since the base drives both alike), off when --rumble is off; a pad not there is ERROR_DEVICE_NOT_CONNECTED as in 401 */
        XbPadState p;
        u32 v = (u32)a->r[2], left, right;
        static int told;
        if (!xb_pad_state((u32)a->r[0], &p)) { *r3 = 0x48Fu; return 1; }
        left = rd16(v);
        right = rd16(v + 2);
        host_pad_rumble((u32)a->r[0], left > right ? left : right, 60000u);   /* held: the game sends 0 to stop */
        if (told++ < 16) port_log("import xam#402 XamInputSetState user %u: motors %u, %u\n", (u32)a->r[0], left, right);
        *r3 = 0;
        return 1;
    }
    case 407:     /* XamInputGetKeystroke(user, flags, XINPUT_KEYSTROKE *) and */
    case 408: {   /* XamInputGetKeystrokeEx(u32 *user, flags, XINPUT_KEYSTROKE *): Too Human's first stop 8312e3bc, A on the title (r4 2). The console turns
                   * each pad's changes into key events, one a call: virtual key (VK_PAD_*), unicode 0, flags (1 down, 2 up), user, HID code 0. Built here
                   * from the same pad state as 401 (the base's, or the script's in a test run): the lowest changed button, trigger (past 30) or left-stick
                   * direction (past 7849) first. Nothing changed is ERROR_EMPTY; no pad at a named user is ERROR_DEVICE_NOT_CONNECTED. Not made yet: the
                   * console's held-key repeats (flag 4) */
        static const u16 bit_vk[16] = {0x5810, 0x5811, 0x5812, 0x5813, 0x5814, 0x5815, 0x5816, 0x5817, 0x5805, 0x5804, 0, 0, 0x5800, 0x5801, 0x5802, 0x5803};
        static const u16 more_vk[6] = {0x5806, 0x5807, 0x5820, 0x5821, 0x5823, 0x5822};   /* LT, RT, left stick up, down, left, right */
        static u32 shown[4];   /* per user: buttons in bits 0..15, then the six of more_vk in bits 16..21, as last reported */
        u32 up = imp->ord == 408 ? (u32)a->r[0] : 0, want = imp->ord == 408 ? rd32(up) : (u32)a->r[0], ks = (u32)a->r[2], u, first, last;
        int any = 0;
        if (want == 0xFFu) first = 0, last = 3;
        else if (want < 4) first = last = want;
        else { *r3 = 0x57u; return 1; }   /* ERROR_INVALID_PARAMETER */
        for (u = first; u <= last; u++) {
            XbPadState p;
            u32 now, diff, b;
            if (!xb_pad_state(u, &p)) { shown[u] = 0; continue; }
            any = 1;
            now = p.buttons | (p.lt > 30 ? 1u << 16 : 0) | (p.rt > 30 ? 1u << 17 : 0) | (p.ly > 7849 ? 1u << 18 : 0) | (p.ly < -7849 ? 1u << 19 : 0)
                | (p.lx < -7849 ? 1u << 20 : 0) | (p.lx > 7849 ? 1u << 21 : 0);
            /* the base moves the left stick with a scripted or keyboard direction as well as the d-pad: a real pad's d-pad gives its key alone, so the
             * stick's key in the same direction is left out (two events a press moved Too Human's menu cursor two or three items) */
            now &= ~((now & 0xFu) << 18);
            diff = (now ^ shown[u]) & 0x3FF3FFu;   /* bits 10 and 11 have no key */
            if (!diff) continue;
            for (b = 0; !(diff & (1u << b)); b++) {}
            shown[u] ^= 1u << b;
            memset(tc_base + ks, 0, 8);
            wr16(ks, b < 16 ? bit_vk[b] : more_vk[b - 16]);
            wr16(ks + 4, (now >> b) & 1 ? 1 : 2);
            wr8(ks + 6, (u8)u);
            if (up) wr32(up, u);
            {   /* the first 64 events go in the log, so a scripted run shows what the game was given (4000 with XB_PADLOG) */
                extern int xb_padlog_on(void);
                static int told;
                if (told < 64 || (told < 4000 && xb_padlog_on()))
                    port_log("import xam#%u keystroke %d: vk %04x %s, user %u (vertical blank %u)\n", imp->ord, ++told, b < 16 ? bit_vk[b] : more_vk[b - 16],
                             (now >> b) & 1 ? "down" : "up", u, xb_frame_number());
            }
            *r3 = 0;
            return 1;
        }
        *r3 = any ? 0x10D2u : 0x48Fu;   /* ERROR_EMPTY, or no pad */
        return 1;
    }
    case 650:     /* XamNotifyCreateListener(mask, 4): first stop 821b8b58 under 820967a0, r3 0xef. The console's system notifications (sign-in, the guide, a
                   * storage device) come through the listener; a waitable handle (listener_new), and 651 has nothing to give until a stop asks for one */
        *r3 = listener_new((u32)a->r[0]);
        return 1;
    case 651: {   /* XNotifyGetNext(listener, id, u32 *id out, u32 *param out): the next one posted to this listener (of that id when it is not 0), read by
                   * 82096848 on the main thread; zero when there is none, and the listener is no longer set */
        u32 id, param;
        if (!note_next((u32)a->r[0], (u32)a->r[1], &id, &param)) { *r3 = 0; return 1; }
        wr32((u32)a->r[2], id);
        wr32((u32)a->r[3], param);
        *r3 = 1;
        return 1;
    }
    case 522:     /* XamUserGetXUID(user, type mask, u64 *): first stop 823290a4 under 821b8198, r3 0, r4 7. The offline xuid of 551; another user stops */
        if ((u32)a->r[0]) return 0;
        wr64((u32)a->r[2], 0xE000000000000001ull);
        *r3 = 0;
        return 1;
    case 551: {   /* XamUserGetSigninInfo(user, flags, XUSER_SIGNIN_INFO *): first stop 821b9258 under 821afb38, r3 0, r4 1, r5 a stack block. The record: xuid, info flags,
                   * state, guest number, sponsor, name[16]. The one local profile of 528, with an offline xuid (top nibble 0xE) */
        u32 o = (u32)a->r[2];
        static const char name[] = "Player";
        unsigned i;
        memset(tc_base + o, 0, 40);
        if ((u32)a->r[0]) {     /* signed out: ERROR_NO_SUCH_USER */
            *r3 = 0x525;
            return 1;
        }
        wr64(o, 0xE000000000000001ull);
        wr32(o + 8, 0);
        wr32(o + 12, 1);
        wr32(o + 16, 0);
        wr32(o + 20, 0);
        for (i = 0; i < 16; i++) wr8(o + 24 + i, i < sizeof name ? name[i] : 0);
        *r3 = 0;
        return 1;
    }
    case 528:     /* XamUserGetSigninState(user): first stop 821b7ed8 under 8209c060, r3 0. One local profile at user 0 (state 1, signed in without Live), so
                   * the game's save path is the one it takes on a console; the other users are signed out (0) */
        *r3 = (u32)a->r[0] == 0 ? 1u : 0u;
        return 1;
    case 642:     /* XamGetSystemVersion(): first stop 821e55d0, which below 0x200A3200 takes the plain import rather than looking one up by ordinal. A version below that */
        *r3 = 0x20076000u;
        return 1;
    case 1:       /* NetDll_WSAStartup(1, version, WSADATA *): first stop 821e5780 under 822075c0, the network start 821b6408 goes on to after XNetStartup. It succeeds and sends nothing */
        return no_effect(imp, r3);
    case 51:      /* NetDll_XNetStartup(1, XNetStartupParams *): first stop 821e55d0 under 821b6408, the game's network start. Nothing is sent: it succeeds, and each later
                   * network call stops until a caller shows what it needs */
        return no_effect(imp, r3);
    case 71:      /* NetDll_XNetQosServiceLookup(1, flags, event, XNQOS **): first stop 823298b4 under 82096848, asked every 6 seconds. The port opens no network: non-zero,
                   * WSAENETDOWN, and 82096848 keeps no result */
        *r3 = 0x2742u;
        return 1;
    case 503:     /* XMsgStartIORequest(app, message, overlapped, block *, block size): first stop 821b7da0, a thread (8209b848) that tells the console a signed-in
                   * user's context: app 0xfb, message 0xb0006, block { user, 0, 0x8001 (the presence context), value }. Rich presence is what friends see on Live
                   * and nothing reads it back, so it succeeds; any other message stops. 0xb0007 is its partner, a presence property (XUserSetPropertyEx:
                   * user, property id, size, value *), first stop at frame 368 after the quote cards, with no overlapped and the block on the stack (r6 700ff7e0).
                   * Xenia's XGI application answers both with success. An overlapped, when given, is marked done (first word 0) as 715 does */
        if ((u32)a->r[0] == 0xFBu && ((u32)a->r[1] == 0xB0006u || (u32)a->r[1] == 0xB0007u)) {
            if (a->r[2]) { wr32((u32)a->r[2], 0); wr32((u32)a->r[2] + 4, 0); }
            return no_effect(imp, r3);
        }
        return 0;
    case 2815:    /* XamPartyGetUserList(1, list *): first stop 8209bfb8 through the table of 407 (822bab50). That caller skips the list on 0x807d0003, the
                   * error for not being in a party */
        *r3 = XB_STATUS(0x807D0003u);
        return 1;
    case 614:     /* XamContentGetLicenseMask (out pointer, 0): first stop 8209ba08 (via the wrapper 821b8330) passes a stack word it zeroed, takes a
                   * non-zero result as failure, then reads bit 0 of the word and caches both in two bytes of a static (+74 asked,
                   * +76 answer). Bit 0 off is the game's "TRIAL VERSION" title and cut menu; a port is the full game: licensed */
        wr32((u32)a->r[0], 1);
        port_log("import xam#614 answered licensed (out word 1)\n");
        *r3 = 0;
        return 1;
    case 526: {   /* XamUserGetName(user, char *, size): first stop 821b7ed0 under 8209beb0, size 16. The name 551 gives */
        static const char name[] = "Player";
        u32 i;
        for (i = 0; i < sizeof name && i < (u32)a->r[2]; i++) wr8((u32)a->r[1] + i, (u8)name[i]);
        *r3 = 0;
        return 1;
    }
    case 715:     /* XamShowDeviceSelectorUI(user, type, flags, u64 bytes, u32 *device, overlapped): first stop 821b91f8 under 820a27d8, the console's
                   * choose-a-storage-device screen. The port has one place to save: device 1, chosen at once. The game takes 0x3e5 (pending) as started
                   * and then watches the overlapped block; its first word 0 is done, no error */
        wr32((u32)a->r[4], 1);
        if (a->r[5]) { wr32((u32)a->r[5], 0); wr32((u32)a->r[5] + 4, 0); }
        note_post(9, 1);   /* the system interface opened and closed: 820a3b28 holds the title until id 9 says closed (byte 87658865) */
        note_post(9, 0);
        *r3 = 0x3E5u;
        return 1;
    case 714: {   /* XamShowMessageBoxUI(user, title, text, count, buttons, focus, flags, u32 *result, overlapped): first stop 821b9200 under 820a2190, the game's
                   * error boxes (save data it cannot read). Text in the log, the focused button pressed at once. The port draws no system box yet */
        char text[256];
        u32 at = (u32)a->r[2], o = (u32)a->s[0];
        int n = 0;
        while (n < 255 && at && rd16(at + 2u * n)) { u16 c = rd16(at + 2u * n); text[n++] = c < 0x7f ? (char)c : '?'; }
        text[n] = 0;
        port_log("message box, %u buttons, focus %u: %s\n", (u32)a->r[3], (u32)a->r[5], text);
        wr32((u32)a->r[7], (u32)a->r[5]);
        if (o) { wr32(o, 0); wr32(o + 4, 0); }
        note_post(9, 1);
        note_post(9, 0);
        *r3 = 0x3E5u;
        return 1;
    }
    case 705: {   /* XamShowKeyboardUI(user, flags, wide default, wide title, wide description, wide *result, room in characters, overlapped): Too Human, first
                   * stop 8312e3dc under 82a46040 <- 828e8020, naming the new character after Start Campaign's class choice (build box A mn18a, r4 flags 4, no
                   * default). The console's own keyboard screen; the port draws none yet, so it is the player pressing Done on what the screen first holds:
                   * the default text, or with none, the profile's name (526), as the game would show it. Logged with the title */
        static const char name[] = "Player";
        char title[128];
        u32 def = (u32)a->r[2], at = (u32)a->r[3], out = (u32)a->r[5], room = (u32)a->r[6], o = (u32)a->r[7], i = 0;
        int n = 0;
        while (n < 127 && at && rd16(at + 2u * n)) { u16 c = rd16(at + 2u * n); title[n++] = c < 0x7f ? (char)c : '?'; }
        title[n] = 0;
        if (room) {
            if (def) for (; i + 1 < room && rd16(def + 2u * i); i++) wr16(out + 2u * i, rd16(def + 2u * i));
            else for (; i + 1 < room && name[i]; i++) wr16(out + 2u * i, (u16)name[i]);
            wr16(out + 2u * i, 0);
        }
        port_log("system screen: keyboard \"%s\", flags %x, room %u: Done with %s (%u characters; the port draws no keyboard yet)\n", title, (u32)a->r[1], room,
                 def ? "the default text" : "the profile's name", i);
        if (o) { wr32(o, 0); wr32(o + 4, 0); }
        note_post(9, 1);
        note_post(9, 0);
        *r3 = o ? 0x3E5u : 0;
        return 1;
    }
    case 709:     /* XamShowAchievementsUI(user): first stop 821b91e0 under 820a2a90, the main menu's ACHIEVEMENTS. A screen of the console's own: the port has none yet,
                   * so it opens and closes at once and the game carries on. The kit screen for this item is still to write */
        port_log("system screen: achievements (none drawn)\n");
        note_post(9, 1);
        note_post(9, 0);
        *r3 = 0;
        return 1;
    case 420:     /* XamLoaderLaunchTitle(name, flags, ...): first stop 821b8ab8 under 82096680, the end of the game's main, r3 NULL: back to the dashboard, the game's
                   * own way out ("Return to Xbox LIVE Arcade"). For a port that is the end of the program; another title has nothing to launch */
        if ((u32)a->r[0] == 0) port_exit("the game returned to the dashboard", 0);
        return 0;
    case 52:      /* NetDll_XNetCleanup(1): first stop 821e56a0 under 821b6498, the game's shutdown after "Return to Xbox LIVE Arcade". The port opens no network */
        return no_effect(imp, r3);
    case 2:       /* NetDll_WSACleanup(caller): first stop 821e5840 on the way out. The port opens no network */
        *r3 = 0;
        return 1;
    case 711:     /* XamShowMarketplaceUI(user, type, offer, ...): first stop 821b91e8 under 820a2b70, the main menu's DOWNLOADABLE CONTENT. The console's store, which
                   * the port has no part of: opens and closes at once */
        port_log("system screen: marketplace (none drawn)\n");
        note_post(9, 1);
        note_post(9, 0);
        *r3 = 0;
        return 1;
    case 606:     /* XamContentGetDeviceData(device, XDEVICE_DATA *): first stop 821b8340 under 82090968, r3 the device 715 chose. Id, type 1, total and
                   * free bytes (u64 at +8, +16), then a wide name: the game asks how much room a save has. Plenty */
        wr32((u32)a->r[1], (u32)a->r[0]);
        wr32((u32)a->r[1] + 4, 1);
        wr64((u32)a->r[1] + 8, 0x100000000ull);
        wr64((u32)a->r[1] + 16, 0x100000000ull);
        wr16((u32)a->r[1] + 24, 0);
        *r3 = 0;
        return 1;
    case 750:     /* XamUserCreateAchievementEnumerator(title, user, xuid, flags, start, count, u32 *size, HANDLE *): first stop 821b8110 under
                   * 820900d8, which reads which awards the signed-in user holds and skips the reading on any non-zero answer. No awards kept yet:
                   * ERROR_NO_MORE_FILES, as the game's path for an empty list */
        port_log("import xam#750 achievement list answered none\n");
        *r3 = 0x12;
        return 1;
    }
    return 0;
}
