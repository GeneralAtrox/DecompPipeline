"""The SDK modules' rows of the game contract (port/CONTRACT.md), for contract_doc.py, which writes the table.
Split from contract_doc.py at its 400-line cap.

Nearest existing: contract_doc.py - it writes the tables and holds the game.json keys; these rows are its, moved
out whole so the key tables fit.
"""

# sdk/NAME.c, the SDK modules a game.json "sdk" may list: name -> (what it replaces, what it needs from the game's
# headers)
SDK_MODULES = {
    "sy_heap": (("`syMallocInit`, `syMalloc`, `syFree`, `syMallocFinish`; the guest C library's `malloc`, `free` "
                 "(Shinobi's, on the same allocator) as `sy_guest_malloc`, `sy_guest_free`"),
                "only those names"),
    "sy_sys": ("Shinobi system services: cable (VGA), config, RTC (host time), TMU timer",
               "`SYE_CBL_VGA`, `SYS_RTC_DATE`; game.json `console`"),
    "sy_chain": (("Shinobi's interrupt chain (`syChainAddHandler`, `syChainDeleteHandler`): the vertical blank "
                  "event runs; the others are kept, logged, never run"),
                 "`SYCHAIN`, `SyChainFunc`"),
    "gdfs": ("gdFs over the disc image, synchronous; CD-DA status only",
             "`GDFS`, `GDFS_DIRREC`, `GDD_DRVSTAT_*`, `GDD_SEEK_*`, `SdkProc`"),
    "adxf": ("CRI's ADX file system (ADXF) over the `gdfs` module, synchronous reads",
             "`ADXF`, `GDFS`; the `gdfs` module in `sdk`"),
    "am_heap": ("`amHeapAlloc`, `amHeapFree` in a host-backed sound RAM",
                "`SdkProc`"),
    "am_sys": (("Audio64's AM start-up (1.x): driver choice, file and memory callbacks, `amInit` loading the driver "
                "image into sound RAM (nothing runs it)"),
               "the registry names in its header"),
    "ac_digi": (("Audio64's AC layer (1.x): digital ports and CD input on the kit's AICA voices, set directly "
                 "instead of queued"),
                "the registry names in its header"),
    "shc_rt": ("SHC compiler helpers: block copy, bit-field reads, unsigned mod, `div`",
               "`ShcDivResult`, `g_errno`, `SHC_ERRNO_DIV0`"),
    "shc_long": ("SHC's register-ABI division and block-move helpers, bit-exact with the original",
                 "`g_errno`, `SHC_ERRNO_DIV0`"),
    "kamui1_scene": ("Kamui 1's vertex buffer, render, flip and frame buffer calls over `pvr/`",
                     "game.json `kamui1`; layouts in `sdk/kamui1.h`"),
    "kamui1_state": ("Kamui 1's render state and strip calls",
                     "`sdk/kamui1.h`"),
    "kamui1_tex": ("Kamui 1's texture surfaces in the emulated texture memory",
                   "`sdk/kamui1.h`"),
    "kamui1_misc": (("Kamui 1's callbacks (end of render, vertical sync), scanline, fog, palette, border, "
                     "background, clipping and register calls"),
                    "`sdk/kamui1.h`"),
    "kamui2_scene": (("Kamui 2 (game-written vertex buffer): `kmSetSystemConfiguration`, scenes and passes, "
                      "`kmRender` (renders `pvr/`, flips at the configured vsync interval), queue/texture status, "
                      "VSync, clip; a vertex buffer configured past main RAM's end is cut to end there (logged) "
                      "instead of wrapping onto the game's code; `kmBeginScene` never sets the system up again (as "
                      "Kamui's does not), so the same config through another address mirror keeps the textures made "
                      "after set-up; a pass whose direct-transfer list has a 0% share (the game feeds that list "
                      "itself) takes the other bank (half the bank when there is only one), so the other lists keep "
                      "the shares the game sized; a list that still runs past its room is logged (`kamui2: list N "
                      "ran M bytes past its room`)"),
                     "`KMSYSTEMCONFIGSTRUCT`, `KMVERTEXBUFFDESC`; layouts in `sdk/kamui2.h`"),
    "kamui2_tex": (("Texture surfaces in texture memory: create (all Kamui 2 types), load, free, frame-buffer "
                    "read-back"),
                   "`KMSURFACEDESC`"),
    "kamui2_misc": (("Device, display mode and its callbacks, border, version, fog, background plane, palette "
                     "(`kmSetPaletteMode`, `kmSetPaletteData` to `pvr/pvr_pal.c`)"),
                    "`KmCallbackFunc`, `KmVersionInfo`"),
    "adx_mw": (("CRI ADX (`ADXT_*`: streamed music read through gdFs, looping, output volume and fades, state, "
                "pause and error code) and Sofdec (`mwPlyCreateSofdec`: a movie player whose methods the game calls "
                "through the handle's table), on `runtime/media`"),
               "`ADXT`, `AdxtErrFunc`, `MWS_PLY_INIT_SFD`; the `gdfs` module in `sdk`"),
    "maple_pad": (("The Maple bus (`pd*`): standard controllers on ports A..D from `base/input.c`, sampled once per "
                   "`pdExecPeripheralServer`; a vibration pack in each socket 2; the VMU LCD (`pdVmsLcd*`) for the "
                   "cards `--vmu` places. A game that starts the bus through an entry the port does not bind (a "
                   "newer library's `pdInitPeripheral` variant) gets its controllers at the first server pass or "
                   "read, logged once"),
                  "`LibPadDevice` (the record type `pdGetPeripheral` returns), `PDS_VIBPARAM`"),
    "bu_vmu": (("Shinobi's memory card library (`bu*`) on host files, one 128 KB VMU image per card in the save "
                "directory (an emulator's file), and the one game file a card holds (`buSaveExecFile`, "
                "`buRewriteExecFile`, `buLoadFileEx`)"),
               "the registry names in its header; `BUS_BACKUPFILEHEADER` for file images"),
    "sy_font": (("The BIOS font in the boot ROM (`syBtFntGetAddr`, `syBtFntGetInfo`, `Sdk_BiosFontGetAddress`, "
                 "`Sdk_BiosFontLock`, `Sdk_BiosFontUnlock`): an open 12x24 face, written by `tools/gen_sy_font.py`; "
                 "Shift-JIS with no glyph is blank and logged"),
                "the registry names in its header"),
    "vmu_flash": ("The VMU flash file system on a card image (used by `bu_vmu`)",
                  "none (`sdk/vmu_flash.h`)"),
    "fm_mng": (("The flash manager (`fmMng*`) over the console's 128 KB flash ROM image (`--flash FILE`, else a "
                "stock console's)"),
               "the registry names in its header"),
    "lib_ctx": ("The library's execution contexts (`Lib_InitContext`, `Lib_SwapContext`) as host coroutines",
                "the registry names in its header"),
    "net_none": ("The network library with no modem: every request fails, so the game takes its no-network path",
                 "the registry names in its header"),
}
