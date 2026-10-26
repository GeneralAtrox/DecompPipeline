# What this target consulted

DecompPipeline is the user's own work. Every file in this target was written for DP. Nothing below was copied:
code, tables and wording were all written fresh from the description each source gives. Third-party tools were
read to learn facts and to cross-check results only.

| Source | How it was used | Copied |
|---|---|---|
| IBM PowerPC architecture books I-III (user ISA, virtual environment, operating environment) and the PowerPC 64-bit ELF ABI | the semantics of every runtime helper (`tc_ppc.c`), the instruction classes (`ppcscan.py`), the p-code audit, the calling convention, the stack-argument layout | no |
| Xenon / VMX128 public descriptions (the Xbox 360 CPU's vector extension: 128 registers, opcodes 4/5/6) | which encodings are vector ones (`ppcscan.classify`); the phase-2 design | no |
| FIPS-197 (AES) | `aes.py`: the S-box computed from GF(2^8), the key schedule, the inverse cipher; the FIPS-197 test vector | no |
| The published XEX2 layout (header, optional headers, security info, basic/normal compression, import records) | `xex.py` | no |
| The published XDVDFS layout (volume descriptor, binary-tree directories, partition offsets) | `xiso.py` | no |
| The Microsoft PE/COFF specification | `pe.py` (`pe_sections`, the `.pdata` table) | no |
| Ghidra 12.1.2's PowerPC Sleigh files (`ppc_64_be`, `ppc_common.sinc`, `altivec.sinc`, `g2.sinc`, A2ALT) | read to audit Ghidra's p-code for the instructions phase 1 allows (DESIGN.md §5) and to confirm there is no VMX128 decoding | no |
| `External/xenon-dp/DESIGN.md` beside the projects (an earlier research note) | orientation only. Its claim that Too Human uses LZX was checked and found wrong (it uses basic compression). | no |
| XenonRecomp outputs (`External/` beside the projects), and its seed list `extra_seeds.txt` | cross-check only: the seed list was compared with DP's own catalog (7 of 182 seeds are DP entries; 171 lie inside `.pdata` functions); the extracted `default.xex` matched DP's byte for byte | no (XenonRecomp's source was not used) |
| DP's own `targets/gamecube_ppc/` | the model for the target layout, the judge glue, the runtime API and the tests | DP's own code, adapted |
| DP's `tests/test_saturn_sh2.py` | the pattern of the offline tests (MSVC through `vcvars`, a runner on a synthetic image) | DP's own code, adapted |
