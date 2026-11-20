# Screen copies: address-keyed, then direct hand-off

Written 2026-11-08 for the Xbox 360 kit. The user's decision: "Reusing the xenia architecture is a no. We should aim to
make our own like we are doing now. Then we have options to optimise it and configure it. Xenia should still be used
to discover behaviour." So the kit's design is our own. Xenia's source is read to learn what the console does, and
none of its classes or structure are carried over.

## What the console does

The GPU draws into its on-chip memory (EDRAM). A screen copy (a "resolve") writes a rectangle of it into ordinary guest
memory, in a texture format and a tiled layout. Anything that later reads that memory sees the copy: a texture fetch,
another copy, or the CPU. A reader finds a copy only by its address. Nothing on the console ties a copy to the texture
header the game happened to name. Xenia models it the same way (`src/xenia/gpu/d3d12/d3d12_render_target_cache.cc`,
`Resolve`): it works out the destination's address range with the depth format normalised, writes that range into its
copy of guest memory, and marks any texture that overlaps the range as needing a reload.

The kit used to keep one host texture per header. That breaks when the game points two headers at one block of memory,
or re-points a header: a reader can then pick up the wrong copy (the bk4 question in Too Human's black frames).

## Layer 1: bring-up, exact behaviour (address-keyed)

- **The table.** Each copy is entered under its guest address range, with format, tiling, size and the frame it was
  made in. Depth copies are entered with the format the reader will see.
- **Reads.** A texture fetch looks up its own address range. The newest copy that overlaps it wins, whatever header
  either side used. With no copy, the fetch reads guest memory as before.
- **Memory.** A copy also lands in guest memory, untiled and converted as the console would leave it, when anything
  outside the GPU may read it (the CPU, a save, a later copy by address). Until a range is known to be GPU-only, it is
  written.
- **Switch:** `XB_RESOLVE_ADDR` (on once proven; `=0` keeps the old per-header model for comparison).

## On-chip memory (EDRAM)

Added 2026-11-08. The source side of a copy. On the console a render target header is only a view of EDRAM: a base
tile, a pitch, a multisample mode and a format, over tiles of 80x16 samples. Two headers that cover the same tiles
see the same contents, whichever one drew them, and a copy reads whatever the tiles hold at that moment.

Too Human's in-game opening depends on this. Its scene target (640 wide, 2x multisampled, base tile 0, tiles 0-735)
is copied out with a clear. A second header (1280 wide, not multisampled, base 0, tiles 0-719) is then copied over
the same memory, and the fog pass draws onto it. Nothing draws into the second header first: the only draw between
the copies writes no colour channel. So on the console the second copy reads the cleared tiles. The kit kept one host
texture per header, so it read a texture that nothing had ever written, and the frames went black.

- **Records.** Each colour target header gets a record with its base and extent in tiles, worked out from its pitch,
  multisample mode, format width and height.
- **Sharing.** Headers at the same base whose host textures are the same size share one host texture.
- **Ownership.** Each draw and clear stamps its record as the newest writer. When a header is bound, or read by a
  copy, and another record at the same base wrote after it, it takes that record's picture first. The newest writer
  owns the tiles, and a copy reads the owner.
- **Overlaps at another base** are logged, not copied. The host keeps each surface as a picture, not as tile memory, so
  a view that starts part-way into another has no picture to take. Depth surfaces are not in the model yet.
- **Write masks.** Draws keep the channels their colour write mask leaves out (render states 212-224), each target
  with its own mask.
- **Switch:** `XB_EDRAM=1` (`xb_d3d_edram.h`). Off by default: with it off, the kit behaves exactly as before.
- **Cleared tiles.** A whole clear marks its record as holding one value. A header that reads those tiles later, whatever
  its pitch, multisampling or host size, and also one at another base that the cleared range covers, is filled with that
  value instead of a copy of the picture. EndTiling's copy reads the tiles' newest writer first, as Resolve does.
- **Clear value.** A copy that clears (Resolve or EndTiling, flags 0x100) leaves the game's clear colour as the target's
  format holds it: 2_10_10_10_FLOAT (7e3) keeps red, green and blue from 0 to 31.875 and alpha in two bits, a half-float
  target clamps to its range, and a NaN becomes 0. With the switch off the host clears with the floats as given.
- **Scene marker.** `XB_MARK_PS` (the game's table; Too Human: the fog pass `fab6ceb9`) or `XB_MARK=key` names a pixel
  shader; the frame it starts to be drawn is logged as `scene marker N`. `XB_DUMP_AT=fog+0,fog+50`, `XB_DIAG_EDRAM=fog+1`
  and `XB_DIAG_PS=key@m3` count from it, so runs whose loading times differ are compared at one scene.
- **Diagnostics.** `XB_DIAG_EDRAM` also logs each distinct clear once (raw words, floats, NaN, infinity or out-of-range,
  the value as held), and `XB_DIAG_PS` reads back the logged draw's render target 0 after it.
- **Status, 2026-11-08: not proven, so it stays off.** Same build, off (fm1) and on (fm2), boot through Start Campaign:
  - The scene copy clears to 1, 1, 1, 1 (words 3f800000), format 7e3, bias 0: inside the format's range. No clear in the
    opening is NaN, infinite or half-float, and the fog pass's inputs and output hold no NaN or infinity.
  - The fog pass first draws at frame 3009 (off) and 2580 (on). From that frame on, both runs show black: the copy the
    fog pass reads (slot 0) is black (0.004 for one frame, then 0, off; 0 on), and so is its output.
  - Xenia shows a uniform pale grey for about 250 vertical blanks after the loading screen, then the fortress in fog.
    The kit's pale grey with the switch off (192 of 255 against Xenia's 193) matches it; with the switch on that stretch
    is black, so the model is wrong there.
  - Fixed-clock runs do not reach the same scene at the same frame, and the guest's header and copy addresses differ
    between runs: compare by the scene marker.
  - fx1 (cleared-tiles fix, switch on; the diagnostic at the marker + 1) shows the frame's order: the scene target is
    cleared to 0, 0, 0, 1, drawn, copied twice with the white clear (one copy per tile), then EndTiling copies it again
    and clears it to 0, 0, 0, 1. Only then does the 1280-wide header's copy run, so the newest writer of the tiles is
    EndTiling's black clear, not the white one, and the fog pass still reads black. Against Xenia aligned by the
    marker: the grey stretch (marker -300 to -134) is black from -209 to -59, and everything from the marker to +1500
    is black, where Xenia shows the fortress in fog. The main menu and character creation match ed3. So it stays off.
    If EndTiling's clear really is the last write, the console's second copy would be black too. Xenia shows that it
    is not, so the kit's order of the tiled commands is the next thing to check against a Xenia GPU trace of that frame.
    The trace answers it: see "The console's order" below.
  - Copying a picture between different layouts is a stand-in. The console would show the same tiles scrambled. Only
    a uniform clear reads the same in any layout, which is the case Too Human relies on.

## The console's order (Xenia GPU trace, 2026-11-08)

Taken with Xenia's `--trace_gpu_frames` on build box A, from fog frames (swap 3400 of one run, 3115 of another, the same
order in both) and one loading-screen frame (swap 3200, the same tiled chain). Guest addresses are Xenia's run; the
kit's differ. One frame of the opening scene, in packet order:

1. Bin select 3 (tile 0, left half). The scene draws into the 640-wide 2x target, base tile 0.
2. Depth copy, raw, to `1e610000` (1280x720, 8_8_8_8). The same copy predicated for tile 1 is skipped here.
3. Scene colour copy, sample 4, with a colour clear: to `1e9a8000`, 16_16_16_16_FLOAT, clear `d8060180` (1, 1, 1, 1).
4. Occlusion-query draws, colour write mask 0.
5. EndTiling's copy, colour and depth clear: to `1dd2a000`, pitch 1280, format k_8, clear `c0000000` (0, 0, 0, 1).
6. Bin select c (tile 1, window offset and scissor on the right half): steps 1-5 again, to `1e624000`, `1e9d0000`
   and `1dd2f000`, rect 639.5 to 1279.5.
7. Bin select all. Query draws, then a depth-only rectangle (the depth clear).
8. **The fog pass** draws into the 1280-wide 1x target, base 0. It samples t0 = `1dd2a000` (EndTiling's copy, white)
   and t1 = `1e9a8000` as 16_16_16_16_EXPAND (the scene copy from step 3).
9. One more pass into base 0, sampling the depth copy.
10. Copy base 0 to `1e9a8000`, 2_10_10_10, exponent -5.
11. The 320x180 bloom chain, copied back to `1e9a8000`; then the overlay draws.
12. Copy with clear to the front buffer, then swap.

There is no copy into the scene texture between EndTiling (step 5) and the fog pass (step 8). Nothing copies the
1280-wide header before the fog pass draws into it.

**Where the kit departs.** The kit's fx1 log at marker + 1 matches steps 1-7: one tile replayed with predication 3
and c, the white scene clear, and EndTiling's black clear. It then runs `Resolve(flags 0)` of the 1280-wide header
into the scene texture (`02538000` in that run), unpredicated, before the fog pass. The console has no such command.
That copy reads tiles whose newest writer is EndTiling's black clear, so with `XB_EDRAM=1` it overwrites the scene
copy with black, and the fog pass reads black. With the switch off it copies the 1280-wide header's own stale host
picture instead (the pale grey). The black from marker -134 to -59 is very likely the same extra copy in the frames
before the fog. The kit's per-draw log covers only marker + 1, so this is not yet proven. The console frames in that
stretch (loading screen, first fog frames) have the same tiled chain with no extra copy.

**The caller (rc2, `XB_DIAG_RESOLVE`, 2026-11-08).** Not the tiling path: the game picks tiled or plain copies by its own
argument (829a5858, 829a5970), and the kit already takes the tiled branch. The extra copy is the game's scene-texture
object (vtable at 82255d24) refreshing itself: 82991d18, which draws a material, finds a sampler bound to the scene
texture and calls the object's method at +4 (8298f2f8). That copies the object's own 1280-wide surface into the scene
texture when the object's flag at +0x30 is set. Every material draw sets the flag (method +0x10). Only the bloom pass
(82990f40, method +0x14) and the copy itself clear it. The chain is 8298f2f8 under 82991d18 under 8297cca0, and the fog
pass is such a material. In rc2 the copy runs from frame 378 to marker -215, is absent from -214 to -1, and returns at
the marker, so it does not cause the black that starts about 50 frames before the marker. Why the console's flag is
clear at the fog pass is answered below. The skip stand-in is not to be built (coordinator, 2026-11-08).

**K8 state (paused 2026-11-08 for the front end; nothing built, nothing live).**
- *Flag order is the same on both.* rt3 (`XB_TRACE` + `XB_TRACE_AT`, marker + 1, frame 2383, one host thread) and a
  Xenia call log (XeniaRedux `--call_log_functions`, run cv2) agree: 82992088 (pass BEGIN, from 829732b0) sets the flag
  and stores the config object at this+0x1d4; 82990f40 (bloom) and 8299da50 clear it; 82990d00, 82992638, 82993728
  and 8296aa98 (twice, frame end) set it again.
- *The difference is r27 in 82991d18.* The material draw calls REF (+4) only when one of the effect's pixel-shader
  parameters (permutation 0x2800 of effect *(r5+0x3d0), found by 82856460, entries 0x8c bytes) has +0x7c equal to the
  word at 8338be00. That word is the hash (8281af60) of the lower-cased "PreviousLightingTexture", set lazily by
  82816528. On the console the fog draw does not call REF; in the kit it does, which makes the extra copy. In menus
  both refresh (console vblanks 2459-5891), so the kit's menu copies are right.
- *Next step, not run:* rt4 with `XB_TRACE_PARAMS` (kit 0fa404f8, `xb_game.h`) to log which parameter matches the hash
  at marker + 1 and + 2, then the same walk on the console call log. Expected causes: the hash word set earlier in the
  kit than on the console, or a parameter list built from a different permutation. The fix is behind a switch, proven
  against Xenia's xgp2 sheet (marker 6434, an estimate).
- *The black 49 frames before the marker* did not reproduce in rt3 (-57: mean 246, -7: 239, black from +43). rc2's grey
  (-99) and black (-49) frames have identical copy orders, so no copy explains it. Separately, the kit's frames before
  the marker are too bright (white) where Xenia shows the fortress in fog. Open.
- *Builds:* `XB_LINK_SEC` (default 600) bounds the link; the TH harness uses 1800 because build box A at 100% CPU links the
  88,000 objects in over 600 s (rt1 killed, rt2 void with a partial 330 MB exe; rt3 linked in 4 min 25 s, 237 MB).

## Layer 2: native, for the 144 fps target (direct hand-off)

Once a copy's reader is known, the host's own surface goes straight to that reader. That skips the trip through guest
memory, the tiling and the format conversion, and frees resolution and aspect ratio from the 1280x720 memory layout. For
example, Too Human's depth copy to `016c0000` is read by the fog pass (pixel shader `fab6ceb9`); the hand-off binds the
host depth texture to that sampler.

- **Each hand-off is named and switched on its own:** `XB_HANDOFF=<name>[,<name>...]`, and `XB_HANDOFF=none` turns all
  of them off. A hand-off names the copy (its address range and format) and the reader (the shader and sampler).
- **Unknown copies fall back to layer 1.** A copy with no hand-off, or one whose range or format differs from the
  hand-off's, takes the address-keyed path.
- **Off by default until proven,** then on by default with the switch still there.

## How a new hand-off is proven

1. Layer 1 alone first: the scene is right by address, judged by eye and against Xenia's picture where one exists.
2. Replays with the hand-off off and on, at the original resolution and shape, over the stretch of the game where the
   copy is made. Every frame is equal (a hash per frame; differences listed by frame and region), and the game's log is
   equal.
3. Nothing outside the reader reads the range during those replays. The run watches the range for CPU reads and other
   copies, and any hit keeps the layer-1 write in place.
4. The frame time with it on is recorded beside the 144 fps target (`docs/RUNTIME_TARGET.md`).
5. The plan records the hand-off: its name, the copy and its reader, the replays used and the result.

Then it can be on by default. A later change to resolution or aspect ratio is judged by the game's own rules
("Any aspect ratio" in `docs/RUNTIME_TARGET.md`), not against layer 1's 1280x720 frames.
