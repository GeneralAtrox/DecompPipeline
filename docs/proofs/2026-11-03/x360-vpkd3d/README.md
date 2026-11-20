# vpkd3d128 in the Xbox 360 translator (2026-11-03): the C pack equals the oracle's on 43,488 cases

Too Human's bring-up stopped on 829e4f08 (under the stream thread 829b89e8), which the translator refused:
"vpkd3d128 at 829e4fdc: the Direct3D pack formats are not modelled". The translator now has `xv_vpkd3d`
(`translator/xenontranslate/vmx_unpack.py`), written from the oracle's executor `TcVmxFloat.vpkd3d` (types 0..5, pkFix, pkHalf,
placement by mask and z). As with `vupkd3d128`, the judge still refuses a function with it (`TcFixups.refuseVector`: the
semantics come from Xenia, not the architecture), so such a function is translated but not judged.

`pk_check.py` (build box A, JDK `<tools>\dp\jdk-21.0.12.1+1`, cl from `core.check.msvc_env`) compiles the oracle's own
TcVmxOp..TcVmxFloat with `PkCheck.java`, which prints `vpkd3d` for every type, mask and z over 53 edge words (NaNs, infinities,
signed zeros, denormals, clamps, the fixed-point bases, half-precision ties and denormals, 65504..65536) and 400 random vectors
each. A C program built from `vmx.helper_text(["xv_vpkd3d"])` compares lane by lane.

| Helper | Cases | Differ |
|---|---|---|
| `xv_vpkd3d` as committed | 43,488 | 0 |
| planted: half ties round up (`tie`) | 43,488 | 58 |
| planted: fixed point rounds, not truncates (`trunc`) | 43,488 | 4,078 |
| planted: mask 3 z 3 writes lane 2 (`lane`) | 43,488 | 2,718 |
