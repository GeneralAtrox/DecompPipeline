"""Saturn hooks (core/target_hooks): only which functions a scoped oracle change reaches.
"unlisted_call_args": TcTrack's CA lines at a call no callee record lists (TcTrack.of, 2026-11-07) reach only a
function core/unchecked_args.sh_in_scope names (the SH-2 image is big-endian). Every other function makes the same
bytes, so its stamp stays as it was. Any other scope counts as in scope, as it did without this module (a scope with
no flags and no hook reached every function).
"""


def stamp_scope(cfg, addr, scope):
    if scope == "unlisted_call_args":
        from core import paths, unchecked_args
        return unchecked_args.hook(cfg, addr, paths.FUNCTION_BODIES, 2, True)
    return True
