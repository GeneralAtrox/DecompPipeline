# Kamui 2: lists start on a 32-byte line

`port/sdk/kamui2_scene.c` `lists_base`. The kit took the vertex buffer's address as the first list's start. Toy Commander
hands in `Mem_AllocAligned`'s result, an allocation + 4 (`acbc5f84`), so every list in the port started 4 bytes into a line.
Nothing else in the port makes a window pointer leave its line.

## The console never has that state

Flycast RAM on the original game, tc-regress's 12 dumps during the intro movie (MAGNUS `tcaudio\emu\out3..out5`): the buffer the
game passes is `acbc5ec4`, the same + 4, and every list pointer at 8c0fce04..8c0fce14 is on a line: `e0bc6060 e0d06540 e0daa6c0
e0cd7700 e0d7b880`, `e0dd18a0 e0f11e20 e0fb5f00 e0ee2f40 e0f870c0`. The port's matching pointer was `e0bc6044`. The dumps
prove the line, not which way Kamui rounds; it is rounded up here because rounding down would overwrite the byte
`Mem_AllocAligned` keeps below the block. The size shrinks by the same bytes, so the buffer's end does not move.

## What it explains

Three symptoms that looked like game or judge problems were this:

- Readable 8c07557c drew the movie quad white (`p & ~31`, correct on a line).
- The translator's store-queue model lost the clip's y1 and drew a top strip of the second movie.
- Round 5's readable 8c0757c8 tinted the truck: its `lead` loop copies a block's tail below r14, which the port's window, plain
  RAM, keeps. With r14 on its line the loop never runs.

## Proof on Toy Commander

Kit 0ce49036 plus this change, game tc-round5 with round 5's 8c0757c8 put back (the file that tints), against the same build
without the change: frames 1000 and 5000 of the bisect's script differ from rc by 0 pixels; without the change they differ by
0.54% and 4.57%. The run reaches vblank 6200 and logs no list past its room. Other games: Power Stone 2's buffer (`acd00000`,
banks of 1572864) is on a line and a multiple of 32, so this changes nothing; Spawn is Kamui 1, not this file.

Kamui 1's `make_lists` has the same shape but no game has handed it an off-line buffer. It is left alone.

Nearest existing: `k2_vertex_buffer_size` in `kamui2.h` (trims the size to RAM's end; it does not touch the start).
