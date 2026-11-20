/* What the kit knows of this game by address. Each game has its own folder, chosen by gen_xbox.py --game. */
#ifndef XB_GAME_H
#define XB_GAME_H
#define XB_GAME_NAME "Too Human"
#define XB_GAME_NET 0            /* xb_net*.c are written against Mega Man 10's session code and memory: XB_NET is ignored */
/* The game's own XMA driver (829fdbc0 reads register 0x1800, 82a06940 register 0x1818) starts under 82773778. For bring-up only, its registers are
 * plain memory, so the driver runs and decodes nothing: no sound. The play path needs a cut above this driver, as Mega Man 10 has above XACT */
#define XB_XMA_WINDOW 1
/* The intro logos are WMV3 (Windows Media Video 9) 720p movies in Video\ (MSGS_Logo_whtFadeOut_720p30_5.1AUD, sk_valkrye_logo, Dolby_bumper_argon),
 * decoded by the game's own player into three 8-bit planes (Y 1280x720, U and V 640x360) and drawn by one full-screen quad with this run-time
 * pixel shader. Since a970b91c the translated decoder's planes match Xenia's (40-43 dB, no macroblock off), so the quad shows the movie;
 * XB_MOVIES=0 draws it black, as every build before did */
#define XB_MOVIE_PS 0xf789b3bbu
/* xb_flow.c: the screen flow from the game's own markers. Its menus are Anark Gameface scenes (TH1Menu_*.bgf) whose Lua scripts call C
 * functions the game registers by name (each record a 0x40-byte name, then the function: "playmovie" at 83253698). {function, Lua argument
 * logged (0 none), what}: the movie the intro scene plays and stops, the menu's sound events, and its PRINTF lines. Then the text that names
 * a screen: the main menu's intro sound starts on the title (the Yggdrasil tree, "Press START"), and START or A on it goes on to the menu */
#define XB_FLOW_CALLS {0x82924fb8u, 1, "playmovie"}, {0x82925080u, 0, "stopmovie"}, {0x829226b0u, 2, "event"}, {0x82920838u, 1, "print"}
#define XB_FLOW_LO 0x829226b0u
#define XB_FLOW_HI 0x82925080u
#define XB_FLOW_STATES {"IntroSoundStart", "title"}, {"MAINMENU Intro Tree - START Press", "main menu"}, {"MAINMENU Intro Tree - A Press", "main menu"}
#define XB_MARK_PS 0xfab6ceb9u   /* the scene marker (xb_d3d.c): the fog pass, first drawn in the in-game opening */
#define XB_RS_ROWS 0x832554B8u   /* xb_state.c: the rows 827570a0 sets the device's states from, same layout and tables as Mega Man 10's */
#define XB_SS_ROWS 0x83255978u
/* The device's vertical blank callback, which 829a4aa0 stores inline (SetVerticalBlankCallback) and the library's handler 8274f528 calls: 829a4a88,
 * which sets the event Too Human's main thread waits on in 8296b5e0 when a frame was quicker than a 60th of a second */
#define XB_VBLANK_CALLBACK 0x40a0u
/* The device's depth-control word (the hardware's RB_DEPTHCONTROL: bit 1 z enable, bit 2 z write, bits 4-6 the comparison), the library's copy of
 * render state 40 and its dirty word's bit for them, as its setters 8274c8c8 (40), 8274c938 (44) and 8274c908 (48) keep them. Too Human also writes
 * the word inline (8299a670, its depth-only pre-pass; 8299a790 per material; 829a10c8), where no state call is made (xb_state.c, xb_gpu_draw.h) */
#define XB_DEV_DEPTHCONTROL 0x2934u
#define XB_DEV_ZENABLE 0x2e64u
#define XB_DEV_DIRTY_DEPTH 0x800u
/* Three more words the game writes inline, each kept by the library's setters as the depth word is (xb_state.c dev_words, xb_gpu_draw.h):
 * - the colour write mask (RB_COLOR_MASK, four bits per target: 8274ce70..8274cf30, render states 212..224, the values also at +0x2e54..+0x2e60,
 *   dirty bit 1 << 37). 8298a120 and 8299a670 clear target 0's bits for the depth-only pass and 829943b8 sets them back;
 * - the colour control (RB_COLORCONTROL: bits 0-2 the alpha comparison 8274c6c0 (104), bit 3 alpha test 8274c200 (96), bit 4 alpha to coverage
 *   8274d820 (336), bits 24-31 its offsets 8274d850 (340), dirty 0x200). 8299a790 turns alpha test on inline for a material whose flags ask for it;
 * - the mode control (PA_SU_SC_MODE_CNTL: bits 0-2 the cull mode 8274c1a0 (56), 3-10 the fill modes 8274c1d0 (52), dirty 0x40). 8299a5f8 sets
 *   the cull mode inline per material */
#define XB_DEV_COLORMASK 0x28dcu
#define XB_DEV_COLORMASK_COPY 0x2e54u
#define XB_DEV_COLORCONTROL 0x293cu
#define XB_DEV_MODECONTROL 0x2948u
/* A stop lists the runs of this word in guest memory and the file reads on each (xb_scan_fill): the "0000" text found where the
 * loader thread's pointers should be, in most runs before frame 1 */
#define XB_STOP_FILL 0x30303030u
/* The XEX's resources, in the image: header key 000002ff of default.xex */
#define XB_SECTIONS {"01D7DC73", 0x833A0000u, 0x14u}, {"MyDigest", 0x833A0080u, 0x20u}, {"4D5307DE", 0x833A0100u, 0xB46FFu}, {0, 0, 0}
/* Its thread-local data, header key 00020104: the image's copy, and how much each thread gets */
#define XB_TLS_RAW 0x83390800u
#define XB_TLS_SIZE 0x450u
/* XB_TRACE's parameter walk (xb_call.c): the material draw 82991d18 refreshes the scene texture (8298f2f8) when a pixel-shader parameter of
 * permutation 0x2800 of its effect (r5+0x3d0) has the name hash at 8338be00, the hash of "PreviousLightingTexture" (82816528) */
#define XB_TRACE_PARAMS {0x82991d18u, 2u, 0x3d0u, 0x2800u, 0x8338be00u, 0x8cu, 0x7cu}
/* Another XDK build, so the same calls at other addresses. Each row is this game's entry, then the case label of xb_d3d_res.h or
 * xb_d3d_draw.h (Mega Man 10's address, used only as the call's name) that does what it does. A library_entry stop adds one. */
#define XB_LIB_CALLS \
    {0x827577d0u, 0x827577d0u}, \
    {0x827570a0u, 0x827570a0u}, \
    {0x82746938u, 0x82746938u}, \
    {0x8274e5d8u, 0x821c9bf0u}, /* SetViewport: first stop 829a4aa0, the block at its object +0x8c; the body reads x, y, w, h, z range */ \
    {0x82758c10u, 0x821cd520u}, /* create a vertex declaration (elements): first stop 829a4aa0, one element (type 002c23a5) on its stack; the body counts to stream 0xff */ \
    {0x82746f28u, 0x821d4cc0u}, /* SetRingBufferParameters: first stop 829a4aa0 after the declaration, its object +0x58; reads +4 .. +20 as Mega Man 10's, 0 is success */ \
    {0x82755338u, 0x821d5110u}, /* (device, 2): first stop 829a4aa0 after the ring call; bits 12..13 of +0x2aa4 and one packet, as Mega Man 10's */ \
    {0x8274f410u, 0x8274f410u}, /* first stop 829a4aa0 right after the devices: a target that is not the device's own (+0x39f0, depth +0x39e8) to none, then samplers, shaders and fetches; its own case */ \
    {0x8274e890u, 0x821ca348u}, /* SetRenderTarget(device, index, surface): first stop 8296a520, (device, 0, device +0x5fa0, its own back buffer); stores at +0x3098 + 4 * index */ \
    {0x8274ebf0u, 0x821ca070u}, /* SetDepthStencilSurface(device, surface): called by 8296a520 after it; stores at +0x30a8 */ \
    {0x82765b00u, 0x82765b00u}, /* Clear: first stop 8296a520; its own range in boundary.json */ \
    {0x82758c98u, 0x82758c98u}, /* SetShaderGPRAllocation: first stop 8296aa98 */ \
    {0x82758100u, 0x821ccea0u}, /* SetVertexShaderConstantF(device, first, floats, count): first stop 8296aa98, (12, 22c78a30, 4); stores at 16 * (120 + first) */ \
    {0x827581e0u, 0x821ccf78u}, /* SetPixelShaderConstantF: the same body at 16 * (376 + first) */ \
    {0x8275e9e0u, 0x821d24b0u}, /* Resolve(device, flags, rect, texture, point, level, slice, colour *, z, stencil): first stop 829a5858, flags 0x14 into the device's own +0x5fa0 */ \
    {0x82758590u, 0x821cd088u}, /* SetPixelShader(device, shader): stores the shader at device +0x3194; its headers are type 7 */ \
    {0x829fc210u, 0x829fc210u}, /* BeginVertices, Too Human's own copy: its range in boundary.json */ \
    {0x8274f7c0u, 0x8274f7c0u}, /* (device, 0, 0): first stop 8296a718 after the last Resolve, right before Swap; maps the presentation interval (+0x351c) and appends packets */ \
    {0x8274fd28u, 0x821ce2f0u}, /* Swap(device, front buffer texture, 0): called by 8296a718 after 8274f7c0; the fence ring at +0x5478/+0x547c as in Mega Man 10 */ \
    {0x82761d90u, 0x82761d90u}, /* SetPredication(device, tile mask): first stop 829a5858 with 3; its own case */ \
    {0x82761f00u, 0x82761f00u}, /* BeginTiling(device, flags, count, rects, colour *, z, stencil): first stop 8296a520 after its Clear; its own case */ \
    {0x827623a0u, 0x821d0708u}, /* EndTiling(device, flags, rects, texture, colour *, z, stencil): first stop 8296a8d0 with 0x300, no rects; per tile, SetPredication and Resolve */ \
    {0x82758938u, 0x821cd248u}, /* SetVertexShader(device, shader): first stop 829a6240, stores the shader at device +0x3198 and patches its constants into +0x480; its headers are type 6 */ \
    {0x8274f350u, 0x821c65e8u}, /* AddRef(device): first stop 82c37840 under the loader 82c36780; four instructions, +0x3c plus one, returns the count */ \
    {0x8274f368u, 0x8274f368u}, /* Release(device): first stop 82c45d78 on the job thread 82c44ee8; its own case */ \
    {0x82744750u, 0x82744750u}, /* Release(resource): first stop 82c370d8, a kit vertex buffer at zero; its own case keeps the kit's and destroys the game's */ \
    {0x82754448u, 0x82754448u}, /* GetDisplayMode(device, 0, block *): first stop 82c37248; its own case */ \
    {0x82744860u, 0x821d73f8u}, /* CreateVertexBuffer(length, usage): first stop 82c37248, (0x78, 0); the same 0x20 header as Mega Man 10's, from XMemAlloc */ \
    {0x82744928u, 0x821d8300u}, /* lock a vertex buffer (header, offset, size, flags): first stop 82c37248; 82c373c8 copies 0x78 bytes to what it returns, the data at +0x18 */ \
    {0x82744978u, 0x821d74c0u}, /* unlock (header): 82c373c8 right after that copy; the body only flushes the data's cache lines (827444f0) */ \
    {0x8274e778u, 0x8274e778u}, /* GetStreamSource(device, stream, offset *, stride *): first stop 82c373c8; its own case */ \
    {0x8274e658u, 0x821c9428u}, /* SetStreamSource(device, stream, buffer, offset, stride, dirty bits): first stop 82c373c8 after GetStreamSource; stores at +0x30ac + 4 * stream */ \
    {0x8274e800u, 0x821c9548u}, /* SetIndices(device, index buffer): first stop 82c389a8; stores at +0x3094 after fencing the old one with a ring packet */ \
    {0x82758b08u, 0x821cd418u}, /* SetVertexDeclaration(device, declaration): first stop 82c389a8 with one the kit made; stores at +0x2e2c, dirty bit 0x80000 of +0x10 */ \
    {0x829fc6f0u, 0x829fc6f0u}, /* BeginIndexedVertices, Too Human's own copy beside BeginVertices: first stop 829b0650 in the level load; its range in boundary.json */     {0x829fcbd0u, 0x821d18f0u}, /* DrawVertices(device, primitive type, first vertex, vertex count): first stop 82c389a8, a triangle list; its range in boundary.json */ \
    {0x82746ed0u, 0x82746ed0u}, /* InsertCallback(device, type, callback, context): first stop 82c389a8 after its draw; its own case */ \
    {0x82743190u, 0x82743190u}, /* BeginCommandBuffer(device, buffer, flags, tags, tags, rects, count): first stop 829ae698 on the worker 829a6758 at frame 11; xb_d3d_cb.h */ \
    {0x82743650u, 0x82743650u}, /* EndCommandBuffer(device): 829ae728 */ \
    {0x82743a40u, 0x82743a40u}, /* RunCommandBuffer(device, buffer, ...): 829ae2f0 on the render thread */ \
    {0x829fcfc0u, 0x829fcfc0u}, /* DrawIndexedVertices(device, primitive type, base vertex, start index, count): first stop 82999c98; its range in boundary.json */ \
    {0x82746e58u, 0x82746e58u}, /* IsFencePending(fence): first stop 8296c898 on the render thread; its own case */     {0x82746a38u, 0x82746a38u}, /* BlockOnFence(device, fence, ...): first stop 82744270 (a texture's cache flush) on the render thread, once fences count; its own case */ \
    {0, 0}
#endif
