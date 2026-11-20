# Texture packs

Split from port/README.md at its 400-line cap.

A pack is a folder: `index.txt`, one `KEY FILE` per line (`#` starts a comment), and the PNG images it names. KEY
(16 hex digits) is the FNV-1a 64 hash of the texture control word's format bits (`tcw & 0xfe000000`), the width,
height and stride (little-endian u32s), then the texture memory bytes the decoder reads from the TCW address (a
16-bit twiddled mip chain from its 1x1 level, 6 bytes in). The VQ codebook is part of it. An image may be any size:
higher-resolution packs work the same way. Every image gets its whole mip chain when it is read (area-weighted, colour weighted by
alpha, logged with the read) and is minified trilinear whatever the guest texture's filter, so a large image drawn small
keeps clean edges; magnification keeps the guest's point or bilinear sampling. The chain is stored beside the image as
`FILE.mips` by `port.exe --pack-mips DIR` (`tools/pack_builds.py` runs it after a game's pack is built), so a run reads it
instead of building it on the frame that first draws the image (2048x2048: 19-50 ms each on the user's laptop). The file
is the same renderer code's output (`pvr/pvr_pack_png.c`): little-endian `PMIP`, a version, width, height, level count,
the PNG's size, the packed size and the FNV-1a 64 of the PNG's last 4096 bytes (which hold the zlib Adler-32 of every
pixel row), then the RGBA8 levels below the top one, gradient-filtered and packed with XPRESS Huffman (Windows'
Compression API). One that doesn't match its PNG (changed since, older version) is ignored; a pack
without them, or an `over` line's image (the game's texels go into it), has its chains built on the loader's thread.
`--pack-mips` reads every line whatever its `if=` setting (its run is a check run, where every setting is off). The log
line of each read ends `stored` or `built (why)`. `port.exe --pack-mips-check DIR` also builds every chain again and
compares it with what a run reads back. A release ships the files with their images.

Images load on a thread of their own (`pvr/pvr_pack_load.c`): the PNG read, the chain and the D3D11 texture are made
there, so the frame that first draws an image only makes a view of it. A load is kicked as soon as the image's key can
be known: Kamui's `kmLoadTexture` hashes the texture as its bytes land (usually in the game's own loading frames) and
starts its image; where the game's own library copies the bytes (raw C writing through `port_texture_written`), the
write that reaches the last texel byte of a surface `kmCreateTextureSurface` made does the same; a texture with no such
word is kicked by its first draw. A draw whose image is not ready waits for
it, so every run shows the same pixels and the game's own texture never shows first. An `over` line's image loads
there too: its kick decodes the game's texels it goes over (at kmLoadTexture, the bytes its key was made from), and the
loader merges them before it builds the chain. Each image's log line gives the
blank it was kicked at, the blank it was ready at, the blank it was first drawn at, the ms the draw waited and the
loader's read, chain (file, unpack, unfilter) and upload ms; the exit report sums the waits. At most 16 images are kicked
and not yet drawn, and the thread holds one image's decoded pixels at a time (freed once its texture is made). With
the pack off no thread starts. `tools/texture_pack.py` computes the key from a texture's bytes as the
game hands them to kmLoadTexture (`kamui_vram`, `kamui_tcw`), writes the PNGs and the index (`Pack`), and checks a
pack (`texture_pack.py check DIR`). The renderer hashes a texture only when it decodes it, which is after the guest
wrote its memory, so an unchanged texture is never hashed again; the log names the pack and its first hit.
An index line may end in a variant word, `KEY FILE xbox` or `KEY FILE playstation`: it applies only while that style
of controller is in use (`--pad-style auto|dreamcast|xbox|playstation`; auto follows the type SDL reports for the device
last used, or before that the first connected gamepad, and only in a run that reads real input, so a check run, replay or
headless run never has one; the keyboard, a raw joystick and any other pad are the game's own art; a test's swaps are
`--pad-style xbox@4500,playstation@4700`), a variant line wins over the line without one for its key, and with no variant
in use nothing changes. The Input menu's Controller pictures (`--pad-art on|off`, saved as `pad-art`, on by default)
shows the game's own art when off; button names still follow the pad. The word `over` after the variant keeps the game's
texture under the image: alpha 0 shows the game's texel, any other pixel is the image's (alpha 1 clears), and the image
is any whole multiple of the texture's size, so a pack changes part of a texture without holding the rest of the game's art.
A line may also end in `rim=RRGGBB`, for a model that draws its sides untextured right after the textured face: those
vertices take that colour, scaled by their strongest channel so the game's lighting stays.
An index line may also carry `if=game.NAME` (`texture_pack.Pack.add(..., when="game.NAME")`): it applies only while that
setting is on, as the Game menu shows it (`port_menu_check_on`, `runtime/console_dc.c`). With a game.json `menu_checks` entry
`{"label", "setting": "game.NAME", "default": true}` that is the clearer-content rule: the game's redrawn art is on by
default, the Game menu check turns it off (saved as `game.NAME=off`), and the pack is read again at once. Off, the line is
skipped, so the texture is the game's own exactly; a check run never loads a pack, and never has a default on. Other lines
of the same pack (pad pictures, say) are unaffected. `pvr/pvr_pack_line.c` reads a line's words.
A line may end in `wide=W` for an image wider than the texture, a backdrop with art for wide frames: the image's middle
W columns are the texture, so 4:3 shows exactly them. In a wider frame a draw spanning the screen's width grows about the
centre with its texture coordinates in step, up to the frame's edges or the image's, and the scene's other draws that span the
screen's width (a highlight bar) grow with it (`pvr/pvr_wide.c`). The log has one line per image the run reads, with its
size and the time the read took.
`tools/pad_icons.py` draws an Xbox-style and a PlayStation-style pad (own simple drawings, no manufacturer art) into the
boxes of a game's controller texture and writes both variant lines, e.g. Spawn's player-count screen: key
`342a3e1bf2e7794b`, `--size 256 --top 0,128,122,128 --front 135,187,121,69 --flip-v`. All the draws of a texture show
the same variant, so a screen that draws four slots from one texture shows one style on all four, and with two different
pads that is the one last used. `--art SPEC.json --art-dir DIR` places a game project's own controller images instead of
drawing, with `over` lines; those images stay in the game's pack, never in this repository, and a `NOT_FOR_RELEASE.txt`
in the pack's folder makes `tools/package_release.py` refuse it until the user has settled a release.
`--texture-keys DIR` lists the textures a run decodes (a PNG each, named by its key, and `keys.txt` with the size,
format and first vertical blank), so a pack for a disc game can start from a replayed scene. Don't commit a pack made
from a game's textures or other people's art. `tests/test_pvr_pack.py` builds the renderer's lookup with its decoder;
a game can run that driver over its own textures (before/after sheets).

**Packs in a release.** game.json's `texture_pack` path exists only on the machine that built the game, so a release
carries its packs beside the program, in `packs\<folder>\`: the game's default under its own folder name, found there
first, and any number of optional packs. The Display menu's Texture pack entry offers Off, Game's default and each
optional pack by its folder name (15 at most, or 14 for a game with a default). Off is off for every pack. An optional
pack shows over the game's default, which still gives the keys the optional pack lacks; it stays off until the player
chooses it, and the choice is saved as `texture-pack=NAME`, unless game.json `texture_pack_choice` names it: then a
play run starts with it until a choice is saved (Game's default turns it off; a check run never has it). `--texture-pack NAME` is the same choice on a command line,
and a saved name whose folder has gone leaves the game's default. `--texture-pack DIR`, a path, replaces the game's
default as before, for a pack in the making.
`tools/package_release.py --exe PORT.EXE --out ZIP [--game-pack DIR] [--pack NAME=DIR]...` makes the release ZIP: the
program, the DLLs beside it and, from each pack, only `index.txt` and the images it names. An optional pack must also
have a `NOTICE.txt` crediting the owners of what it holds, which ships in its folder and goes in the release's
description (`publish-release.ps1 -NotesFile`).

**Packs a game builds itself.** A pack that mixes the game's own new art with the disc's textures can't be committed,
because the disc's part is the player's. Instead the game keeps the sources and a builder, and names them in game.json:
`"pack_builds": {"HD art": {"tool": "../art/hd/tools/hd_pack.py", "inputs": "../art/hd"}}`, with paths relative to
game.json. After a successful compile `tools/build.py` runs `python TOOL --disc DISC --kit-tools <kit>/port/tools
--out <build>/packs/NAME`. DISC is `--disc`, or else game.json `disc`. It runs only when a stamp changes: the files
under inputs (skipping `__pycache__` and `_work*`), the tool, `texture_pack.py`, and the disc's path and size. The new
folder replaces the old pack only when the tool exits 0. A failed build keeps the old pack and writes the tool's
output to `packs/NAME.log`. A missing disc is reported and skipped, never a failed build (`tools/pack_builds.py`,
`tests/test_pack_builds.py`). The pack is an optional pack like any other: it stays off until the player picks it, or game.json `texture_pack_choice` names it. A NAME equal to the folder name of game.json `texture_pack`
is the game's default pack instead (found in `packs/NAME` beside port.exe first), on with no choice; when the build could
not make it, `packs/NAME.skipped` holds why, and the game log says so and that the original picture shows.
