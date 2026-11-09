# Level toolkit: one editor for every console and every 3D game

**Status: approved by the user 2026-11-08, with Toy Commander as the second game. Paused** (the user: "i want to pause
work once robopit is in a good enough state to consider the test finished"). Nothing below is built yet beyond Robo Pit's
one-arena editor (port/base/level_edit.*, port/saturn/sat_level*, Robo Pit's port/level/). Not started: the three-layer
split, the KitIsGameFree extension, Toy Commander's adapter.

## The question: can every 3D game use the same level format?

**Yes, as the format we edit and exchange. No, as a replacement for each game's own files.** I agree with the
Coordinator's answer. I differ on three points, marked **Differs** below.

- **One shared scene format.** The editor and the tools speak only this format. It is glTF 2.0 (`.gltf` plus `.bin`),
  which Blender opens. Robo Pit's `level_rt.py gltf` already writes one.
  - It holds nodes with transforms, meshes, materials and textures.
  - It also holds a `DP_level` extension:
    - at the scene: the game, the console's number format, and the game's file it came from;
    - on each node: the entity's tag (spawn, mesh, object, pickup, blocker, trigger, path, light), its collision
      shape, and any game-specific fields.
- **Differs, exact numbers:** glTF stores 32-bit floats, and those cannot hold every Saturn 16.16 value exactly.
  - So the exact value lives in the extension as the game's own integer, for example
    `"fx": [131072, -10665984, -202899456]`.
  - The glTF transform is derived from it, so Blender can see and move the object.
  - If Blender moves an object, the importer turns the new float into the nearest fixed-point value. An untouched
    object keeps its integer, so native → shared → native stays byte-identical. That is the proof already run for
    Robo Pit.
- **Fields the adapter doesn't understand are kept verbatim.**
  - Robo Pit's v0 file already does this with `merge`.
  - In the shared format, unknown bytes from a native record go into the node's extension as a hex string and are
    written back as they were.
- **Each game still loads its own files, so replays and goldens stay exact.**
  - An edited level reaches the game through the kit, behind a switch that is off by default. For Robo Pit that is
    `--level-mod on`, proven by replay.
  - The game's data on the disc never changes.
- **Differs, meshes are read-only in version 1:** the editor and Blender can see geometry and textures, but only
  placement, collision shapes and entities are saved.
  - Writing a changed mesh back needs a native encoder for each console. For Saturn that is VDP1 quads and colour
    modes; for Dreamcast, PVR strips and twiddled textures. Each one is its own project.
  - HD textures belong to the port's texture-replacement path, not to the level format.
- **Differs, "per-console" model reading only holds where the console's SDK fixes the model format.**
  - The Saturn's SGL model format is shared by every game that uses SGL, so a Saturn hook can read models.
  - Dreamcast, PS2, GameCube and Xbox 360 games each have their own engine's format.
  - There the console layer gives textures (PVR decode) and floats, and reading models is the game adapter's job.
    Toy Commander's `model_read.cpp` already does it for that game.

## The three layers

What exists today, from a search of DP. The details are in `.work/level-editor/out/toolkit-survey.md`.

| Layer | Holds | Comes from |
|---|---|---|
| 1. Editor, in DP's port base (console- and game-free) | viewport, Outliner, Details, gizmo, grid and snap, undo, collision display, Add blocker, save, Play; glTF read and write; level-file helpers that keep unknown fields | `port/base/level_edit.*` (exists); the helpers (`field`, `patch`, `merge`, `write_atomic`) and `--level-test-*` moved from `sat_level*.c`; a small C glTF writer and reader (nearest existing: `level_rt.py gltf`) |
| 2. Console hook, in the kit | the number format (Saturn: 16.16, +Y down); texture decode; on consoles whose SDK fixes the model format, model → mesh | Saturn: `variant()`, `extent()`, RGB555 from `sat_level_scene.c`; a VDP1 texture decoder in `port/imgtool/src/dpimg_tex.c`, lifted from `sat_vdp1.c texel()`. Dreamcast: `dpimg_decode_pvr()` (exists) |
| 3. Game adapter, in the game's own folder | the schema (addresses, record layouts, entity kinds, collision rules, words such as "robot" and "fight"); the native ↔ shared round trip; where the game reads its level, and the hook that applies an edited one; how Play reaches a fight; blocker enforcement | Robo Pit: `port/level/` (schema, `level_rt.py`, DESIGN.md), plus the Robo Pit code that today sits inside `sat_level*.c` |

Interfaces (C):
- **Editor ↔ console:**
  - The existing `LevelEdProvider {label, open, save, play, close}` stays.
  - `LevelScene` gets a `words` block (the actor noun, the Play label) and takes its `unit` from the console. That
    removes the "robots" and 65536 now in `level_edit*.cpp`.
- **Console ↔ game:**
  - A `LevelGameHooks` table registered by the game: `read(scene)`, `write(scene)`, `apply()`, `play_steps`,
    `hold(actor, next_pos)`.
  - The Saturn kit calls it at the points the schema names.
- **Checks:**
  - `KitIsGameFree` grows to catch Saturn addresses (`0x06…`) and the words of every game in the catalogue. Today it
    only knows Dreamcast addresses and a few names, so the leaks in `sat_level*.c` pass it.
  - `level_edit.*` gets a module row in `port/README.md`.

## What a new game must supply

1. A schema JSON: where each level's objects, spawns and lights live, their record layout, and the entity kinds with
   their collision shape. All of it comes from the decompiled code, not from the user.
2. A round-trip script: native → glTF with `DP_level` → native, byte-identical on every level of the game.
3. A model reader, if its console doesn't fix one; textures come from the console layer.
4. One hook where the game builds its level, plus a blocker test, if it has collision to obey.
5. Play's route: the auto-load steps into a match on the edited level.
6. Proofs:
   - the switch absent: zero differences;
   - a moved object's collision moves with it;
   - a blocker stops the actor;
   - Play reaches the edited level through the launcher's path.

## What doesn't fit

- **Scripted logic:** mission scripts and triggers that are code rather than data. The editor can show a trigger
  volume, but not edit what it does.
- **Streaming worlds:** levels loaded in pieces as you move. Version 1 edits one loaded area at a time.
- **Engine-specific data:** navigation meshes, AI paths, baked lighting and visibility tables.
  - Moving an object can leave these stale.
  - The adapter must either rebuild them or lock the objects they depend on, and say which.
- **Mesh edits, until a console has a native encoder** (above).

## What the shared format buys

- Blender round trips for placement and blockers.
- Mod packs that load beside the original, which stays the default, and HD packs through the texture path.
- One viewer for every game.
- Cross-game tools that read one format, such as the bot navigation statistics.
- A new game costs an adapter, not a new editor.

## Second game: Toy Commander (Dreamcast)

- **Why it proves the design:** it is a different console and a different kind of engine (floats, PVR textures, its
  own model format rather than an SDK's), with large house levels instead of one small arena.
- **What already exists:**
  - The Dreamcast kit is the most mature in DP.
  - Toy Commander already has a model reader and exporter (`model_read.cpp`, `model_export.cpp`) and per-level GHQ
    banks (`toy_ghq`).
- **Open question:** its object placement inside those banks isn't decoded yet. That is the first job, from the
  decompiled code.
- **The alternative:** Power Stone 2 is an arena fighter like Robo Pit, so it tests generality less. Its stage packs
  aren't decoded and it has no model reader.

## Order once the user approves

1. Land Robo Pit's one-arena editor (in progress: waiting for the main-laptop slot).
2. Split Robo Pit into the three layers.
   - No behaviour change, proven with the same proofs: load then save byte-identical; 445 shots with 0 differences;
     blockers; Play.
   - Add glTF as the shared file, with the v0 file converted.
3. Extend `KitIsGameFree`.
4. Toy Commander's adapter, only after the user's OK on this note.

## Appendix: what already exists in DP (survey, 2026-11-08)

Read-only search of DP (level-editor branch, 8fa1ca71) for the three-layer split. Input to the design note, written
after the one-arena editor lands.

### Layer 1: console-free editor (base)
- port/base/level_edit.h/.cpp: LevelVert/LevelMesh/LevelTex, LevelEnt, LevelScene (unit, y_down, grid steps,
  place_tmpl) and LevelEdProvider{label, open, save, play, close} set with level_edit_provide().
- Game words leaked into level_edit*.cpp:
  - "robots" (~684, 1339), "fight in this arena" (1345), "robot uses its own" (1448);
  - default unit 65536 (1708, 1714), "Saturn textures are small" (703).
  These move to the provider.
- port_start_again (host_online.cpp), autoload.c and session.c's notes: how Play and replay work.
- scene_dump.c/scene_json.c are the Copy agent prompt, not geometry. Only the JSON writer is reusable.
- KitIsGameFree (tests/test_port_kit.py) checks only Dreamcast addresses and a few words. It misses Saturn 0x06
  addresses and Robo Pit words, so it must be extended.

### Layer 2: per-console hook
- Saturn: sat_level_scene.c variant() (SGL model to textured triangles), extent(), RGB555, 16.16 and box_of() are
  generic. sat_vdp1.c texel() decodes every VDP1 colour mode, but reads live VRAM only.
- Dreamcast: pvr/pvr_twiddle.c pvr_decode(). imgtool/src/dpimg_tex.c dpimg_decode_pvr() is the offline decoder,
  and its comment says the other consoles go there, so a VDP1 decoder does too.
- Game model readers: Toy Commander src/model_read.cpp + model_export.cpp (GHQ to OBJ/MTL/PNG), Vigilante 8
  tools/vehicle_model.py, Robo Pit tools/mdldat.py.
- Xbox 360 has only its D3D9-to-D3D11 layer. GameCube, PS1 and PS2: nothing.
- There is no glTF writer in DP. The only one is Robo Pit's level_rt.py, which exports the arena to glTF.

### Layer 3: per-game adapter (Robo Pit is the only one)
- rp/port/level/: robopit.level-schema.json, level_rt.py (export, import, check, glTF), DESIGN.md.
- gen_saturn_level.py level_edit_h() (called by gen_saturn.py) turns the schema into C macros. game_config.py has no level key yet.
- Robo Pit code still hard-coded in sat_level*.c, which moves to the adapter:
  - arena header offsets, 12-byte texture and layout records, 6-byte kind entries, MAXREC 28, texno 69;
  - robot ids in hold(), entity ids, the floor cap, grid steps, the enter/leave hooks.
- Generic code in sat_level*.c that moves to the base: field, id_of, three, patch, merge (keeps unknown fields),
  write_atomic, and the --level-test-* options.
- No other game has level parsing. Power Stone 2's STG packs are not decoded. Toy Commander has per-level GHQ banks
  (toy_ghq).
