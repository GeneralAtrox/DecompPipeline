# Keyboard and mouse

The kit's keyboard layout and the other controls are in port/README.md, "Controls".

The mouse presses nothing in the kit's own layout. A game's layout (game.json `keys`, which also sets its arrows) or the
player's changes give each mouse input targets: the buttons, a wheel notch (one press each) or any button of the pad. The middle
button dragged turns player 1's right stick, which stays turned until a middle click; the recording carries this
(`HOST_PAD_MOUSE_LOOK`), so a replay gives it back. Input > Keyboard and mouse lets each key or mouse input take any
targets, with Reset to the game's layout; the choices are saved as `kbm-<source>`. game.json `keys` and `key_ramp`
(how fast a held stick key reaches full deflection) are in CONTRACT.md. A test drives keys and mouse with
`--kbm-inject`, since a scripted run reads no real input.

Mouse steer (`base/input_kbm_steer.c`): a game calls `input_kbm_mouse_steer(on)` (base.h) every frame, on while the mouse
should steer (an aircraft, say). Then the mouse moving with the middle button up turns player 1's left stick (right is x
positive, forward is up, y negative), added to the stick keys', by the pixels moved since the last read times Input >
Keyboard and mouse > Mouse sensitivity (1 to 10), eased so a steady hand holds a steady stick and a still mouse is back in
the middle within a few reads; Invert mouse Y flips forward. Mouse steering (on by default) turns it off. It works only
while the window has the focus and the kit's menu is shut, and the window keeps the pointer meanwhile. Set before the pad is
recorded, so a replay needs no settings; `--kbm-inject MOUSEMOVE=DX:DY@V+N` drives it in a test.

Menu pointer (`base/input_kbm_pointer.c`, contract 2.12.0; the user, 2026-11-13: "Pause menu does not support mouse
control"): a game calls `input_kbm_menu_pointer(&menu)` (base.h `PortMenuPointer`) every frame a menu of entries is up, with
each entry's rectangle in its 640x480 screen and the entry selected now, and `input_kbm_menu_pointer(NULL)` when none is.
Then the pointer moved onto an entry steps the selection there with the d-pad (UP/DOWN, or LEFT/RIGHT for a row; the shorter
way round when the menu wraps), a left click on an entry does the same and then presses the confirm button (A, or the
entry's own press, such as RIGHT for an option row) once the selection is that entry, a click off every entry does
nothing, and the wheel steps one entry a notch instead of pressing its layout targets. The right button and the keys stay
the layout's; a key pressed stops the pointer's steps, and the pointer resting where a menu opens moves nothing. The left
button held on as the menu shuts presses nothing until let go. The window maps the pointer into the 640x480 screen through
where the picture sits at any shape (`host_mouse.cpp`, `present_aspect.c` `gfx_screen_point`). Mouse steer waits while a
menu is up, so the pointer stays shown. All of it is presses of player 1's pad before recording, so a replay needs
nothing; `--kbm-inject MOUSEPOS=X:Y@V` puts the pointer there in a test. A game that never calls it is unchanged.

Text, the game's mouse and its menu commands (`native_game.h`, contract 2.17.0). All of it is taken at the tick's pad
read only, never in an in-between frame, and recorded in the session's sixth section EVENTS (`base/session_events.c`);
a replay gives the recording's and ignores the window's, and its game log says "replay uses text/mouse events".
- Text: `pg_native_text(1)` while the game's own field is open. SDL's text input is on, and what is typed comes from
  `pg_native_text_take` as UTF-8, with `\b`, `\n`, `\t` and 0x1b for Backspace, Enter, Tab and Esc. Ctrl+V and
  Shift+Insert paste the clipboard's text into the same stream, so a replay never reads the clipboard. The layout's keys
  press nothing meanwhile; pads and `--input` still do. Esc goes to the field; Guide and the pointer open the title bar;
  Alt+Enter, F9 and F11 stay the window's. A test types with `--kbm-inject TEXT=héllo\b\n@20`.
- Mouse: `pg_native_mouse_mode(PG_MOUSE_LAYOUT)` is today's behaviour and the default. In `PG_MOUSE_POINTER` and
  `PG_MOUSE_LOOK` the mouse is the game's (`pg_native_mouse`: moves, buttons, presses, wheel, pointer, `captured`) and
  nothing of the layout's mouse presses anything. LOOK keeps the pointer (relative mode) while captured. Capture is let go
  on focus loss, the kit's menu or the title bar opening, a modal, and while Alt is held; the next click takes it back and
  is swallowed. Sensitivity is the game's, not a player setting. A test moves it with `MOUSEMOVE=DX:DY@V+N` and drops the
  focus with `FOCUSLOST@V`.
- Menu commands: a tagged `PortMenuItem` (game.json `menu_commands`, or a native game's own) goes to the game as
  (tag, index) from `pg_native_menu_take` at its next tick, recorded. A list's `>` nests popups, more than 16 are split
  into "1-16", "17-32", ..., and `pg_native_menu_list` gives a runtime list. `--menu-pick F:Game>Levels>17-32>Level 20`
  takes the whole path (the "17-32" part may be left out).

Known limits:
- IME composition (Japanese, Chinese, Korean input) is not covered: only committed text reaches the game.
- Netplay (`base/net_inputs.c`) carries pads only: text, the game's mouse and menu commands stay on the machine they
  came from.
- Build box B runs started over ssh draw on the Intel chip even with the GPU exports; a test-machine limit, not a player
  fault.
