/* The desktop host (C++20, host/*.cpp): the program's entry, its data folder, the saved settings and the crash
 * reporter, laid out after Lodoss's port (MIT, the user's own). The kit's C side (runtime/, pvr/, sdk/) stays C;
 * this header is the boundary both sides include. */
#ifndef PORT_HOST_H
#define PORT_HOST_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* runtime/main.c: the guest side of start-up, given the arguments the host built */
int port_main(int argc, char **argv);

/* runtime/install.c: the launcher contract (port/README.md). Each returns the exit code: 0, 1, 4 or 5. */
int port_setup(const char *image, const char *work);         /* --setup-only: check, copy to work\assets\disc */
int port_verify_assets(const char *assets);                  /* --verify-assets: check the image in assets */
int port_find_image(const char *dir, char *out, size_t cap); /* the .cue or .gdi in dir (or a folder below) */
extern const char port_version[];                            /* port_entry.c (generated): --version */

/* host/host_phangrade.cpp: Phangrade's game volume (PHANGRADE_SETTINGS), before the sound device opens */
void host_phangrade_start(void);

/* runtime/port_log.c, runtime/main.c */
void port_log_open(const char *path);
void port_log(const char *fmt, ...);
void port_log_flush(void);
void port_exit(const char *why, int code);

/* host/host_window.cpp: the SDL3 window (runtime/gfx/gfx_device.c opens it unless --headless) and its menu bar
 * (host_menu.cpp). gfx_pump (gfx.h) is there too. Options: --window full|fit|Nx|WxH, --fps-counter on|off,
 * --pause-unfocused on|off, --mute-unfocused on|off. */
int host_window_option(const char *arg, const char *value); /* the arguments taken, or 0 */
int host_window_open(const char *title, unsigned *w, unsigned *h, int *fullscreen); /* pixels; 0 on failure */
void *host_window_hwnd(void);                                /* the window's HWND, NULL when there is none */
void host_window_frame(void);                                /* a frame was presented (the frame-rate counter) */
const char *host_window_state(void); /* "full screen", "focused", "not focused", "covered" or "minimized" (for the log) */

/* The kit's C side, set by the options and by the host's menu */
int gfx_resize(unsigned out_w, unsigned out_h); /* gfx_present.c: the output's new pixel size */
void gfx_set_vsync(int on);                     /* gfx_device.c: --vsync */
int gfx_vsync(void);
void extra_set_interp(int on); /* frame_extra.c: --present interp|tick */
int extra_interp(void);
void extra_set_fps(unsigned fps); /* frame_extra.c: --fps N, 0 uncapped */
unsigned extra_fps(void);
void audio_set_volume(int percent, int muted); /* gfx_audio.c: --volume 0..100; muted silences the device */
int audio_volume(void);
void input_set_rumble(int on); /* gfx_input.c: --rumble */
int input_rumble(void);
int input_device_count(void);           /* gfx_input.c: the gamepads and joysticks open now */
const char *input_device_desc(int i);   /* one of them: name, VID:PID, kind */

/* host/host_data.cpp: the data folder (settings, saves, logs, scene dumps) */
const char *host_data_folder(void);

/* runtime/scene_dump.c: "Copy agent prompt" (the menu bar's icon, Help menu, F9) after the next presented frame */
void scene_dump_request(void);

#ifdef __cplusplus
}
#endif

#endif
