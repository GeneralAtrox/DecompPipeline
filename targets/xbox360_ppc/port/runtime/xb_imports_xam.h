/* xb_imports.c, the imports of XAM, the console's system library (one translation unit with it, for its helpers; here for the 400-line cap) */
static int xam_call(const XbImp *imp, const TcArgs *a, u64 *r3, f64 *f1)
{
    (void)f1;
    (void)imp;
    switch (imp->ord) {
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
        *r3 = 0;
        return 1;
    }
    case 650:     /* XamNotifyCreateListener(mask, 4): first stop 821b8b58 under 820967a0, r3 0xef. The console's system notifications (sign-in, the guide, a
                   * storage device) come through the listener; a token, and 651 has nothing to give until a stop asks for one */
        *r3 = handle_new(NULL);
        return 1;
    case 651:     /* XNotifyGetNext(listener, id, u32 *id out, u32 *param out): the next one posted, read by 82096848 on the main thread; zero when there is none */
        if (s_note_read == s_note_write) {
            *r3 = 0;
            return 1;
        }
        wr32((u32)a->r[2], s_notes[s_note_read % 8][0]);
        wr32((u32)a->r[3], s_notes[s_note_read % 8][1]);
        s_note_read++;
        *r3 = 1;
        return 1;
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
                   * and nothing reads it back, so it succeeds; any other message stops */
        if ((u32)a->r[0] == 0xFBu && (u32)a->r[1] == 0xB0006u) return no_effect(imp, r3);
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
