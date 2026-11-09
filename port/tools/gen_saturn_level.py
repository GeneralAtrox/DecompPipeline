"""The level editor's game.json level_edit for gen_saturn.py's sat_game.h (port/saturn/sat_level.c): the level schema's
level as the SAT_LEVEL_EDIT initialiser and the SAT_LEVEL_* names.

Nearest existing: gen_saturn.py - this was its level_edit_h, moved out unchanged (step_c handed in) to keep gen_saturn.py
under its 400-line limit, as gen_saturn_menus.py and gen_saturn_heads.py are.
"""
import json
import pathlib
import sys


def level_edit_h(le, game_dir, step_c):
    """game.json level_edit {"schema": the game's level schema, relative to game.json; "level": its level (absent: the
    first)}, the edited arena and the level editor's Saturn side (port/saturn/sat_level.c), as the SAT_LEVEL_EDIT
    initialiser in LevelGame's order (sat_level_priv.h), plus the level's names. The schema (dp-level-schema v0)
    names, per level: title, file (on the disc), file_base, arena and tier [variable, value], layout {records, defs, max,
    build}, floor [variable, value], spawns {call, pos, step, count, mode, default [two x, y, z, 16.16], radius}, light
    {call, args [x, y, z the game passes], fight (its in-a-fight byte), handle (the editor's handle distance)}, edge {half,
    height}, models {stands, floor, stands_scale_y, floor_y}, kinds [{name, type}], play {menu, cursor [address, item] (the
    menu's remembered cursor, set as it opens), setup (fight setup), pokes [[size, address, value]] (made as setup starts), lead (a button
    pressed through the title until the menu), steps [auto_load-style steps from the menu to setup]}, blockers {collide (the robot-against-objects test, robot in r4), next_pos (robots' next x, y, z, 12 bytes
    each), robot_r, size [half x, height, half z] (Add blocker's box)}, draw {arena (the arena's draw), stands_ptr (the
    pointer to the stands' model pointer), slPutPolygon, slPushMatrix, slTranslate, slPopMatrix}.
    step_c is gen_saturn.py's AutoLoadStep initialiser, for the play steps."""
    if not le:
        return []
    sch = json.loads((game_dir / le["schema"]).read_text(encoding="utf-8"))
    name = le.get("level") or next(iter(sch["levels"]))
    lv = sch["levels"][name]
    def n(v):
        return int(str(v), 0) & 0xffffffff
    def a(v):
        return f"0x{n(v):08x}u"
    lay, sp, li, ed, md, pl = lv["layout"], lv["spawns"], lv["light"], lv["edge"], lv["models"], lv["play"]
    if len(pl["pokes"]) > 8 or len(sp["default"]) != 2 or len(li["args"]) != 3:
        sys.exit("gen_saturn: level_edit: at most 8 pokes, two start points and a three-part light")
    pokes = pl["pokes"] + [[0, 0, 0]] * (8 - len(pl["pokes"]))
    vals = ([lv["arena"][0], lv["arena"][1], lv["tier"][0], lv["tier"][1], lv["file_base"], lay["records"], lay["defs"], lay["max"],
             lay["build"], lv["floor"][0], lv["floor"][1], sp["call"], sp["pos"], sp["step"], sp["count"], sp["mode"]])
    init = ", ".join(a(v) for v in vals)
    init += ", {" + ", ".join("{" + ", ".join(a(v) for v in p) + "}" for p in sp["default"]) + "}, " + a(sp["radius"])
    init += ", " + a(li["call"]) + ", {" + ", ".join(a(v) for v in li["args"]) + "}, " + a(li["fight"]) + ", " + a(li["handle"])
    init += ", " + ", ".join(a(v) for v in [ed["half"], ed["height"], md["stands"], md["floor"], md["stands_scale_y"], md["floor_y"], pl["menu"],
                                             pl["cursor"][0], pl["cursor"][1], pl["setup"], len(pl["pokes"])])
    init += ", {" + ", ".join("{" + ", ".join(a(v) for v in p) + "}" for p in pokes) + "}"
    bl, dr = lv["blockers"], lv["draw"]
    init += ", " + ", ".join(a(v) for v in [bl["collide"], bl["next_pos"], bl["robot_r"]]) + ", {" + ", ".join(a(v) for v in bl["size"]) + "}"
    init += ", " + ", ".join(a(dr[k]) for k in ("arena", "stands_ptr", "slPutPolygon", "slPushMatrix", "slTranslate", "slPopMatrix"))
    q = lambda s: json.dumps(s)
    return ["#define SAT_LEVEL_EDIT {" + init + "}",
            "#define SAT_LEVEL_PLAY_STEPS {" + ", ".join(step_c(st) for st in pl["steps"]) + "}",
            f"#define SAT_LEVEL_PLAY_LEAD {q(pl['lead']) if pl.get('lead') else 'NULL'}",
            f"#define SAT_LEVEL_TITLE {q(lv['title'])}", f"#define SAT_LEVEL_SLUG {q(name)}", f"#define SAT_LEVEL_FILE {q(lv['file'])}",
            f"#define SAT_LEVEL_GAME {q(sch['game'])}", f"#define SAT_LEVEL_SCHEMA {q(pathlib.Path(le['schema']).name)}",
            "#define SAT_LEVEL_KIND_NAMES {" + ", ".join(q(k["name"]) for k in lv["kinds"]) + "}",
            "#define SAT_LEVEL_KIND_TYPES {" + ", ".join(q(k["type"]) for k in lv["kinds"]) + "}"]
