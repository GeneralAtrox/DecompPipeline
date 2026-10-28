# How the media decoders were written

The files in this folder (and `gfx/gfx_movie.c`, `gfx/gfx_movie.hlsl`, `sdk/adx_mw.c`'s use of them) are the kit's
own code, written from the formats' published descriptions. No code was copied, ported or linked from another
project, and none was read while writing them.

## What was consulted

- **MPEG-1 video:** ISO/IEC 11172-2 (the syntax, the variable-length code tables of its annex B, the zigzag scan,
  the default intra quantiser matrix, the reconstruction and mismatch control ("oddification"), motion vector
  decoding and half-pel prediction, the inverse DCT's definition), as the standard and its well-known textbook
  summaries give them.
- **MPEG-1 system stream:** ISO/IEC 11172-1 (pack and system headers, packets, their stuffing, STD and time stamp
  fields, the end code).
- **CRI ADX:** the format's public descriptions (the header's fields and loop block, the 18-byte frames of a scale
  and 32 four-bit codes, the second-order prediction whose coefficients follow from the header's cut-off
  frequency).
- **ITU-R BT.601:** the studio-range YCbCr to RGB matrix the movie shader uses.
- **The games' own calls:** which middleware functions a game calls, with which arguments and in which order, came
  from the games' disassembly and their decomp workspaces (the player table's slots, the status values a game waits
  for).

## How FFmpeg was used

FFmpeg (`ffmpeg.exe`, run as a program) was the reference the decoders were measured against: its decode of every
test stream was compared sample for sample (ADX) and frame by frame (MPEG-1: PSNR, the first differing frame). Its
source was not read. Where a difference showed, the standard decided: one case is FFmpeg's treatment of skipped
macroblocks in B pictures of streams with full-pel vectors, which this decoder handles as the standard says.
