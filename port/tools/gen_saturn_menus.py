"""The Saturn kit's own menus in game.json, for gen_saturn.py's sat_game.h: the kit's entries in the game's menus
(menus, port/saturn/sat_menus.c and sat_lobby.c), the menus' dark panel (menu_panel, sat_panel.c), the opening logos
skipped (movie skip_logos, sat_logos.c) and online play started from the game's own menus (netplay "menu").

Nearest existing: gen_saturn.py's team_fight_h and movie_h - the same game.json section to #define lines, kept here so
gen_saturn.py stays within its size.
"""
import json


def _a(v):
    return f"0x{int(str(v), 0):08x}u"


def menus_h(cfg):
    """game.json menus, the kit's entries in the game's own menus (port/saturn/sat_menus.c), as SAT_MENUS: {"hub": the main
    menu's function, "draw": its draw (the cursor's line), "frame": the game's frame step (it reads the pad), "frame_start":
    [the function the hub's loop starts a frame with, the word it is handed], "edge": the newly pressed buttons (a word,
    active-low), "box": the game's box (x, y, w, h in text cells), "bar": [the cursor bar (x, y in 16.16 pixels, width in
    cells), its pulse counter (a word), its direction (a byte)], "text": {"space": a space's cell in the small type, "first": the
    cell of its letter 0 (letter g is first + 2g: digits from 1, A from 37), "palettes": the palette words, "palette": the slot
    the menu's items use, "map": the text map, "line": its bytes a line}, "sound": [the sound routine, the cursor's sound]};
    "fight_choice" as SAT_MENUS_FIGHT: {"select": the robot select, "mode": its mode for FIGHT, "team_mode": the four-robot
    mode (TRAINING), "cursor": FIGHT's line, "four": the four-robot flag (a byte), "count": the robots in the fight (a word),
    "box": [x, y, w, h] of the 1 VS 1 / 2 VS 2 box, "title": [the four-robot select's title text (past its codes), the words
    2 VS 2 shows there instead], "ladder": 2 VS 2 as a fight on the game's FIGHT ladder (sat_ladder.c) - {"setup": the fight
    set-up, "at": a call it makes after its boss swap and before it loads the robots, "after": the fight's results, "robot":
    one robot's set-up (seat in r4), "draw": SELECT ENEMY's draw of the robot under the cursor (r4 1, record in r5),
    "records": [the robot records, a record's size], "copies": the first record slot the game copies robots into, "sorted":
    [the ranking (words), its length], "seats": the fight's robots (words), "flags": [the robots' flags, their step, the CPU
    flag's offset, the out flag's offset], "boss": [the
    boss-swapped flag (a byte), the opponent it kept (a word)], "won": the won flag, "fields": {"in_use", "listed", "rank",
    "special", "kind", "name"}: offsets in a record, "name_draw": the name routine, "show": [x, y] of SELECT ENEMY's two lines,
    "energy": [energy, robot step], "size": the robots in a fight (4; 6 and 8 take the same rule), "list": the opponent
    list's builder (the player's record in r6), "cursor": [SELECT ENEMY's step each frame (mode in r6), the list's top
    and cursor (words), the offset past the step's r7 of the list's address]} as SAT_MENUS_LADDER};
    "online" as SAT_MENUS_ONLINE: {"move": the hub's cursor move (its first
    argument points at the cursor), "box_h": the hub box's height (a word), "items": [x, first row, count] of the hub's items,
    "box": [x, y, w] of the online screens' box}.
    game.json menu_panel, the menus' half-transparent panel (port/saturn/sat_panel.c), as SAT_MENU_PANEL: {"pmod": its VDP1
    mode, "color": its colour}."""
    out = []
    mn = cfg.get("menus")
    if mn:
        t, b, s = mn["text"], mn["bar"], mn["sound"]
        vals = [mn["hub"], mn["draw"], mn["frame"], mn["frame_start"][0], mn["frame_start"][1], mn["edge"], mn["box"], b[0], b[1],
                b[2], t["space"], t["first"], t["palettes"], t["palette"], t["map"], t["line"], s[0], s[1]]
        out.append("#define SAT_MENUS {" + ", ".join(_a(v) for v in vals) + "}")
        fc = mn.get("fight_choice")
        if fc:
            fv = [fc["select"], fc["mode"], fc["team_mode"], fc["cursor"], fc["four"], fc["count"]]
            out.append("#define SAT_MENUS_FIGHT {" + ", ".join(_a(v) for v in fv) + ", {" + ", ".join(_a(v) for v in fc["box"]) + "}}")
            if fc.get("title"):
                out += [f"#define SAT_MENUS_FIGHT_TITLE_AT {_a(fc['title'][0])}", "#define SAT_MENUS_FIGHT_TITLE " + json.dumps(fc["title"][1])]
            ld = fc.get("ladder")
            if ld:
                f = ld["fields"]
                lv = [fc["select"], ld["setup"], ld["at"], ld["after"], ld["robot"], ld["draw"], ld["records"][0], ld["records"][1], ld["copies"],
                      ld["sorted"][0], ld["sorted"][1], ld["seats"], int(str(ld["flags"][0]), 0) + int(ld["flags"][2]), int(str(ld["flags"][0]), 0) + int(ld["flags"][3]),
                      ld["flags"][1], ld["boss"][0], ld["boss"][1], ld["won"], f["in_use"], f["listed"], f["rank"], f["special"], f["kind"], f["name"], ld["name_draw"], ld["show"][0], ld["show"][1],
                      ld["energy"][0], ld["energy"][1], ld["size"], fc["four"], fc["count"], ld["list"], *ld["cursor"]]
                out.append("#define SAT_MENUS_LADDER {" + ", ".join(_a(v) for v in lv) + "}")
        on = mn.get("online")
        if on:
            ov = [on["move"], on["box_h"]] + list(on["items"])
            out.append("#define SAT_MENUS_ONLINE {" + ", ".join(_a(v) for v in ov) + ", {" + ", ".join(_a(v) for v in on["box"]) + "}}")
    mp = cfg.get("menu_panel")
    if mp:
        out.append("#define SAT_MENU_PANEL {0x%04xu, 0x%04xu}" % (int(str(mp["pmod"]), 0), int(str(mp["color"]), 0)))
    return out


def net_menu_h(np):
    """game.json netplay "menu": "in-game" when the game's own menus start online play (menus "online", sat_lobby.c): the
    build has no Online menu (SAT_NET_IN_GAME). netplay "show", where a game they started shows itself (sat_online.c), as
    SAT_NET_SHOW: {"fight_frame": the function the fight calls once a frame, "status": [x, y, width] of the two lines under
    the hub's box (hosting, open spaces, address), "notice": [x, y, width] of a fight's join and leave notices}. netplay
    "leave": [the buttons (sat_input.c's pad word, 1 = held: B 1, C 2, A 4, START 8), the frames held] that leave an online game, as SAT_NET_LEAVE; "drop_ms": nothing heard for this long and the other
    player is gone (base net_link's default 8000), as SAT_NET_DROP_MS."""
    out = ["#define SAT_NET_IN_GAME 1"] if np.get("menu") == "in-game" else []
    if np.get("drop_ms"):
        out.append("#define SAT_NET_DROP_MS %du" % int(np["drop_ms"]))
    if np.get("leave"):
        out.append("#define SAT_NET_LEAVE {" + ", ".join(_a(v) for v in np["leave"]) + "}")
    sh = np.get("show")
    if sh:
        out.append("#define SAT_NET_SHOW {" + ", ".join(_a(v) for v in [sh["fight_frame"]] + sh["status"] + sh["notice"]) + "}")
    return out


def logos_h(mv):
    """game.json movie "skip_logos": the movies --skip-logos skips at their first call (the opening logos, by file name;
    port/saturn/sat_logos.c), as SAT_MOVIE_LOGOS."""
    if not mv.get("skip_logos"):
        return []
    return ["#define SAT_MOVIE_LOGOS {" + ", ".join(json.dumps(n) for n in mv["skip_logos"]) + "}"]
