"""The C locals that stand for the guest's registers (translate.py's header has the model)."""
import re

from .expr import Var, ZERO

CRB = ("lt", "gt", "eq", "so")
U64 = "u64"


def crname(bit):
    return f"c{bit // 4}{CRB[bit % 4]}"


def vtype(name):
    if re.fullmatch(r"[fp]\d+", name):
        return "f64"
    if re.fullmatch(r"c\d(lt|gt|eq|so)", name) or name in ("ca", "so", "ov"):
        return "flag"
    return "u32"


def R(n):
    return Var(f"r{n}", "u32")


def F(n):
    return Var(f"f{n}", "f64")


def P(n):
    return Var(f"p{n}", "f64")


def CR(bit):
    return Var(crname(bit), "flag")


def CA():
    return Var("ca", "flag")


def SO():
    return Var("so", "flag")


def OV():
    return Var("ov", "flag")


def RA0(n):
    return ZERO if n == 0 else R(n)
