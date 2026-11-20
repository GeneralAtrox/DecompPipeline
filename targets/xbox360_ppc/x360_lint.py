"""The Xbox 360 judge's lint (x360_check.run_check uses it): core/lint's rules with this target's wording and size cap.
In its own file because x360_check imports core.check, which needs a project: the translator's compile_all lints with
this too, without one.
"""
from core.lint import lint as core_lint
MAX_C = 2_000_000  # work.c cap here; core/lint's 60 KB is an agent-readability limit, and 38 Too Human functions
#                    (2,000 to 8,000 instructions) have translator C past it even in the short form


def lint(src, meta):
    """core/lint's rules (R2-R6), with the float rule worded for this target: f64 is the register type here, and the
    size cap is MAX_C (core/lint's 60 KB cap is waived up to it)."""
    out = []
    for e in core_lint(src, meta):
        if e == "work.c is larger than 60 KB":
            if len(src) > MAX_C:
                out.append(f"work.c is larger than {MAX_C // 1000} KB")
            continue
        out.append(e.replace("double precision (use f32 only)", "write f64, the tc_rt.h type, not double"))
    return out