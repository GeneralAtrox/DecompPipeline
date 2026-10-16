"""refine.py sync-names, the text side: rewrite one readable file so it uses the registry's names where it still uses
raw ones (fn_ADDR of a function named since, g_/DAT_ADDR of a named global), and the registry's prototype for its
own function. core/sync_run.py judges the result; nothing here decides that a rewrite is right.

- Comments: the raw identifier is replaced by the name, nothing else.
- The file's own (exported) definition gets the registry prototype when its header differs (a raw name, or a
  prototype the designer changed). Parameters pair with the old ones by storage (where the original takes each
  argument: readable_abi). A parameter of the same type is renamed in the body. When the body already converts the
  old parameter into a local of the new type (`Entity *e = (Entity *)(uintptr_t)param_1;`), that local takes the
  new parameter (or becomes it, when it has the new name). Otherwise the old name stays as a local converted from
  the new parameter at the top of the body (a "shim"). A new parameter the body does not use gets `(void)name;`.
  Returns convert to the new result type.
- A call of a named callee passes its arguments in the registry prototype's order (paired by storage), each
  converted to the new parameter type, and 0 for a placeholder the registry declares (storage "none", or a name
  unused_* in a storage the judge does not compare); a result used in an expression converts back to the raw
  result type. Any other parameter without a raw argument refuses: the judge would not see an invented value.
  `(void)fn_X(...);` is a statement call too; its `(void)` goes when the callee's prototype returns void.
- An old name a designer renamed (the registry entry's "was") is renamed like a global, never as a member.
- Anything else (a function used as a pointer, a global) is renamed.
mode "clean" adds no casts and strips `(u32)(uintptr_t)` from an argument whose new type is a pointer; mode "cast"
casts every argument and result whose type changed. SyncRefused when no mechanical form exists: an f32 meeting an
integer in one storage, an argument with no new parameter, a new name the body already uses for something else.
"""
import re

from . import readable, readable_abi
from .lint import norm_sig
from .raw_names import RAW_FN, RAW_GLOBAL, mask
from .sync_text import (SyncRefused, adopter, close_of, conv, exported, line_span, norm_type, split_args,  # noqa: F401
                        statement_call)


def _uses(code, name, s, e, cuts=()):
    return [m.start() for m in re.finditer(rf"(?<![.>\w]){re.escape(name)}\b", code[s:e])
            if not any(a <= s + m.start() < b for a, b in cuts)]


def own_header(reg, addr, meta, code):
    """(start, ')', '{', '}') of the exported definition when its header differs from the registry's prototype for
    addr (lint R3's comparison), else None."""
    ex = exported(code)
    if ex and norm_sig(code[ex[0]:ex[1] + 1]) != norm_sig(reg.proto(addr, meta)):
        return ex
    return None


class Sync:
    """One file's rewrite. reg: the registry; meta: the function's harness meta; storage_of(callee record): the
    callee's parameter storage (readable_check.callee_storage); names: {raw identifier: name} to rewrite."""

    def __init__(self, reg, addr, meta, storage_of, names, mode):
        self.reg, self.addr, self.meta, self.storage_of, self.names, self.mode = reg, addr, meta, storage_of, names, mode
        self.callees = {c["addr"]: c for c in meta["callees"]}
        self.done, self.shims, self.edits, self.prototype = {}, [], {}, None

    def count(self, raw, what):
        self.done[(raw, what)] = self.done.get((raw, what), 0) + 1

    def run(self, text):
        if self.names:
            pat = re.compile(rf"\b({'|'.join(map(re.escape, self.names))})\b")
            for s, e in reversed(mask(text)[1]):  # comments first: same text, only the identifier changes
                for m in pat.finditer(text, s, e):
                    self.count(m.group(1), "comment")
                text = text[:s] + pat.sub(lambda m: self.names[m.group(1)], text[s:e]) + text[e:]
        self.text, (self.code, _) = text, mask(text)
        ex = own_header(self.reg, self.addr, self.meta, self.code)
        proto = self.reg.proto(self.addr, self.meta)
        if not ex:
            return self.xform(0, len(text), {})
        start, close, open_, end = ex
        head = re.sub(r"\s+", " ", self.code[start:close + 1])
        name = readable.parse_proto(head)[1]
        if name == f"fn_{self.addr}":
            self.count(name, "definition")
        else:
            self.prototype = {"from": head, "to": proto}
        body = self.body(head, proto, open_, end)
        return self.xform(0, start, {}) + proto + self.text[close + 1:open_] + body + self.xform(end + 1, len(text), {})

    def body(self, head, proto, open_, end):
        """The exported function's body {...}, converted from head's parameters and result to proto's."""
        reg, code = self.reg, self.code
        oret, _, oparams = readable.parse_proto(head)
        nret, _, nparams = readable.parse_proto(proto)
        try:
            ost = readable_abi.own_storages(reg, self.addr, head, self.meta)
            nst = readable_abi.own_storages(reg, self.addr, proto, self.meta)
        except readable.RegistryError as e:
            raise SyncRefused(f"placement: {e}")
        if (oret.strip() == "void") != (nret.strip() == "void"):
            raise SyncRefused(f"result `{oret}` became `{nret}`")
        rename, shims, cuts = {}, [], []
        by_storage = dict(zip(ost, oparams))
        for (nt, nn), s in zip(nparams, nst):
            old = by_storage.pop(s, None) if s != readable_abi.NONE else None
            same = old is not None and norm_type(old[0]) == norm_type(nt)
            if old and old[1] == nn:
                if same:
                    continue
                raise SyncRefused(f"parameter `{nn}` keeps its name but changes type: `{old[0]}` -> `{nt}`")
            if old is None:
                if _uses(code, nn, open_ + 1, end) and not self.move_local(nn, open_, end, rename):
                    raise SyncRefused(f"new parameter name `{nn}` is already used in the body")
                shims.append(f"(void){nn};")
                continue
            ad = adopter(code, open_, end, old[1], nt)
            if ad and ad[0] == nn:  # the local that held the converted parameter becomes the parameter
                for a, b, kind in ad[1]:
                    if kind != "init":
                        self.edits[a] = (b, "")
                        cuts.append((a, b))
                self.shims.append(f"local {nn} became the parameter")
            elif _uses(code, nn, open_ + 1, end) and not self.move_local(nn, open_, end, rename):
                raise SyncRefused(f"new parameter name `{nn}` is already used in the body")
            elif ad:  # the local takes the new parameter instead of the converted old one
                a, b, _ = ad[1][0]
                self.edits[a] = (b, f" {nn}")
                cuts.append((a, b))
                self.shims.append(f"local {ad[0]} = {nn}")
            if not _uses(code, old[1], open_ + 1, end, cuts):
                if not ad and (nn in rename or not _uses(code, nn, open_ + 1, end)):
                    shims.append(f"(void){nn};")
            elif same and not (ad and ad[0] == nn):
                rename[old[1]] = nn
            else:
                shims.append(f"{old[0]} {old[1]} = {conv(reg, nn, nt, old[0], 'cast')};")
                self.shims.append(f"{old[1]} ({old[0]}) from {nn} ({nt})")
        for t, n in by_storage.values():  # old parameters with no new one: only `(void)name;` may use them
            voids = [m for m in re.finditer(rf"\(\s*void\s*\)\s*{re.escape(n)}\s*;", code[open_ + 1:end])]
            if len(_uses(code, n, open_ + 1, end)) != len(voids):
                raise SyncRefused(f"old parameter `{t} {n}` has no parameter in `{proto}` and the body uses it")
            for m in voids:
                a, b = line_span(code, open_ + 1 + m.start(), open_ + 1 + m.end())
                self.edits[a] = (b, "")
        ret = (oret, nret) if norm_type(oret) != norm_type(nret) else None
        inner = self.xform(open_ + 1, end, rename, ret)
        blank = re.compile(r"[ \t]*\n[ \t]*\n")
        if blank.match(inner) and not blank.match(self.text[open_ + 1:end]):  # a cut left a blank first line
            inner = inner[inner.index("\n") + 1:]
        if shims:
            lines = [ln for ln in inner.split("\n")[1:] if ln.strip()]
            indent = re.match(r"\s*", lines[0]).group(0) if lines else "    "
            nl = inner.find("\n")
            head_, rest = (inner[:nl + 1], inner[nl + 1:]) if nl >= 0 else ("\n", inner)
            inner = head_ + "".join(f"{indent}{s}\n" for s in shims) + rest
        return "{" + inner + "}"

    def move_local(self, nn, open_, end, rename):
        """When the body declares a local named nn (the new parameter's name), plan renaming it, with every use in
        the body, to an unused name (rename[nn]); returns whether it did. The judge still has to pass the result."""
        code = self.code
        decl = rf"(?<=[;{{}}(])\s*(?:const\s+)?[A-Za-z_]\w*(?:\s*\*\s*(?:const\s*)?|\s+)+{re.escape(nn)}\s*[=;\[,)]"
        if nn in rename or not re.search(decl, code[open_:end]):
            return False
        fresh = next(f"{nn}_local{k or ''}" for k in range(100) if not re.search(rf"\b{nn}_local{k or ''}\b", code))
        rename[nn] = fresh
        self.shims.append(f"local {nn} renamed {fresh}")
        return True

    def xform(self, s, e, rename, ret=None):
        """New text of text[s:e]: raw names rewritten, parameters renamed (rename), returns converted (ret), and the
        planned edits ({start: (end, text)}) applied."""
        # a raw name as a word; an old name a designer renamed ("was") not as a member (.x / ->x) either
        words = [rf"\b{re.escape(w)}\b" if RAW_FN.fullmatch(w) or RAW_GLOBAL.fullmatch(w)
                 else rf"(?<!\.)(?<!->)\b{re.escape(w)}\b" for w in self.names]
        words += [rf"(?<![.>\w]){re.escape(o)}\b" for o in rename]
        words += [r"\breturn\b"] if ret else []
        pat = re.compile("|".join(words)) if words else None
        edits = sorted((a, b, t) for a, (b, t) in self.edits.items() if s <= a < e)
        out, i, code = [], s, self.code
        hits = list(pat.finditer(code, s, e)) if pat else []
        events = sorted([(a, 0, (b, t)) for a, b, t in edits] + [(m.start(), 1, m) for m in hits], key=lambda x: x[:2])
        for pos, kind, data in events:
            if pos < i:
                continue
            out.append(self.text[i:pos])
            if kind == 0:
                out.append(data[1])
                i = data[0]
                continue
            i = self.word(data, e, rename, ret, out)
        out.append(self.text[i:e])
        return "".join(out)

    def word(self, m, e, rename, ret, out):
        """Rewrite the identifier match m (appending to out); returns where the text continues."""
        code, word = self.code, m.group(0)
        if word == "return":
            semi = m.end()
            while code[semi] != ";":
                semi = close_of(code, semi) + 1 if code[semi] in "([{" else semi + 1
            expr = self.xform(m.end(), semi, rename, ret)
            lead = expr[:len(expr) - len(expr.lstrip())]
            out.append("return" + (lead + conv(self.reg, expr.strip(), ret[0], ret[1], self.mode) if expr.strip()
                                   else expr))
            return semi
        if word in rename:
            out.append(rename[word])
            return m.end()
        name = self.names[word]
        par = re.match(r"\s*\(", code[m.end():e])
        if par and RAW_FN.fullmatch(word) and word[3:] in self.callees:
            open_ = m.end() + par.end() - 1
            close = close_of(code, open_)
            statement = statement_call(code, m.start(), close + 1)
            cast = None if statement else re.search(r"\(\s*void\s*\)\s*$", code[:m.start()])
            if cast and statement_call(code, cast.start(), close + 1):
                statement = True  # `(void)fn_X(...);`: its result is discarded
            else:
                cast = None
            out.append(self.call(word, name, open_, close, rename, ret, statement))
            if cast and readable.parse_proto(self.reg.functions[word[3:]]["proto"])[0].strip() == "void":
                self.drop_cast(out, code[cast.start():m.start()])  # no (void) in front of a void call
            self.count(word, "call")
            return close + 1
        out.append(name)
        self.count(word, "global" if RAW_GLOBAL.fullmatch(word) else "reference")
        return m.end()

    def call(self, raw, name, open_, close, rename, ret, statement):
        """Name(args) for raw(args), arguments placed and converted by storage."""
        reg, c = self.reg, self.callees[raw[3:]]
        proto = reg.functions[raw[3:]]["proto"]
        if readable.is_variadic(proto):
            raise SyncRefused(f"{raw} -> {name}: variadic")
        storage = self.storage_of(c)
        try:
            where, _ = readable_abi.callee_where(reg, c, storage)
            place = readable_abi.callee_place(reg, c, proto, storage)
        except readable.RegistryError as e:
            raise SyncRefused(f"{raw} -> {name}: {e}")
        rret, _, rparams = readable.parse_proto(readable.raw_proto(c["signature"]))
        nret, _, nparams = readable.parse_proto(proto)
        spans = split_args(self.code, open_ + 1, close)
        if len(spans) != len(rparams):
            raise SyncRefused(f"{raw}: {len(spans)} argument(s) where its raw signature has {len(rparams)}")
        args = [self.xform(a, b, rename, ret) for a, b in spans]
        compared = {readable_abi.arg_storage(r) for r in c["regs"]}
        new = []
        for (nt, nn), s in zip(nparams, place):
            if s in where:
                i = where.index(s)
                new.append(conv(reg, args[i], rparams[i][0], nt, self.mode))
            elif s == readable_abi.NONE or (nn.startswith("unused") and s not in compared):
                # a placeholder the registry declares (storage "none", or named unused_*) where the original passes
                # nothing the judge compares; any other missing argument is the callee's real input: no invented 0
                new.append("0.0f" if reg.kind(nt) == "f" else "0")
                self.shims.append(f"{name}: 0 for placeholder `{nn}` ({s})")
            else:
                raise SyncRefused(f"{raw} -> {name}: parameter `{nn}` ({s}) has no raw argument")
        for i, s in enumerate(where):
            if s not in place and re.search(r"\(|\+\+|--|[^=!<>]=[^=]", self.code[spans[i][0]:spans[i][1]]):
                raise SyncRefused(f"{raw} -> {name}: argument {i + 1} ({s}) has no parameter and side effects")
        text = f"{name}({', '.join(new)})"
        if statement or rret.strip() == "void":
            return text
        if nret.strip() == "void":
            raise SyncRefused(f"{raw} -> {name}: its result is used, and {name} returns void")
        return conv(reg, text, nret, rret, self.mode)

    @staticmethod
    def drop_cast(out, cast):
        """Remove cast (the text just in front of the call, already in out) from the end of out."""
        call = out.pop()
        done = "".join(out)
        if not done.endswith(cast):
            raise SyncRefused(f"cannot drop `{cast.strip()}` in front of a call")
        out[:] = [done[:len(done) - len(cast)], call]

    def summary(self):
        """[{"from", "to", "what", "n"}] of the renames done (and the own prototype change, if any)."""
        out = [{"from": raw, "to": self.names.get(raw) or raw, "what": what, "n": n}
               for (raw, what), n in sorted(self.done.items())]
        if self.prototype:
            out.append({**self.prototype, "what": "prototype", "n": 1})
        return out
