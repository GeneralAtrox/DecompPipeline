// The program's entry, after Lodoss's main.cpp and desktop_application (MIT, the user's own): install the crash
// reporter, prepare the data folder, then hand the kit's start-up (runtime/main.c port_main) one argument list:
//   port.exe, the defaults, the saved settings, the command line
// The kit parses it in order and a later option wins, so the command line overrides the settings, which override the
// defaults. A scripted run (--fixed-clock other than 0, or --headless) reads no settings and gets no defaults, so
// its result depends on its command line alone; --settings none leaves out the saved settings of a play run. A play
// run's defaults: in-between frames (--present interp: the game's 30 Hz tick, drawn at the display's rate).
//
// The launcher contract (Lodoss's, port/README.md) is handled here, before the kit starts:
//   --version                       prints the build's identity and exits
//   --data-directory DIR            the data folder (settings, saves, logs, dumps) instead of data\ beside the exe
//   --assets DIR                    the disc image is the .cue or .gdi in DIR (what --setup-only copied)
//   --setup-only, --verify-assets   runtime/install.c, without a window or sound
//   --no-dialog                     no message box, even for a crash
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include "host.h"
#include "host_internal.hpp"

namespace {

const char *option(int argc, char **argv, const char *name) // the last value of an option, or nullptr
{
    const char *found = nullptr;
    for (int i = 1; i + 1 < argc; i++)
        if (!std::strcmp(argv[i], name)) found = argv[i + 1];
    return found;
}

bool has_flag(int argc, char **argv, const char *name)
{
    for (int i = 1; i < argc; i++)
        if (!std::strcmp(argv[i], name)) return true;
    return false;
}

// --setup-only or --verify-assets: the exit code (runtime/install.c), logged to the data folder's logs\setup.log.
int install(int argc, char **argv, const char *data)
{
    bool setup = has_flag(argc, argv, "--setup-only");
    const char *disc = option(argc, argv, "--disc"), *assets = option(argc, argv, "--assets");
    if (setup && !data) {
        std::fprintf(stderr, "error: --setup-only needs --data-directory\n");
        return 1;
    }
    if (data) {
        host::prepare_data_dir();
        port_log_open((host::logs_dir() / "setup.log").string().c_str());
    }
    port_log("%s\n", port_version);
    if (setup) {
        if (!disc) {
            std::fprintf(stderr, "error: --setup-only needs --disc\n");
            return 4;
        }
        return port_setup(disc, std::filesystem::absolute(data).string().c_str());
    }
    if (!assets) {
        std::fprintf(stderr, "error: --verify-assets needs --assets\n");
        return 4;
    }
    return port_verify_assets(assets);
}

} // namespace

int main(int argc, char **argv)
{
    const char *fixed = option(argc, argv, "--fixed-clock"), *settings = option(argc, argv, "--settings");
    const char *data = option(argc, argv, "--data-directory"), *assets = option(argc, argv, "--assets");
    if (has_flag(argc, argv, "--version")) {
        std::printf("%s\n", port_version);
        return 0;
    }
    bool installing = has_flag(argc, argv, "--setup-only") || has_flag(argc, argv, "--verify-assets");
    bool scripted = (fixed && std::strcmp(fixed, "0") != 0) || has_flag(argc, argv, "--headless") || installing;
    std::vector<std::string> args{argc > 0 ? argv[0] : "port.exe"};
    if (data) host::set_data_dir(data);
    host::crash_install(!scripted && !has_flag(argc, argv, "--no-dialog"));
    if (installing) return install(argc, argv, data);
    if (!scripted || data) {
        host::prepare_data_dir();
        args.insert(args.end(), {"--log", (host::logs_dir() / "port.log").string()});
    }
    if (assets && !scripted) args.insert(args.end(), {"--seconds", "0"}); // a launcher's play run: no watchdog
    if (data) args.insert(args.end(), {"--save-dir", (host::data_dir() / "saves").string(), "--dump-dir",
                                       (host::data_dir() / "dumps").string()});
    if (!scripted) {
        args.insert(args.end(), {"--present", "interp"});
        if (!settings || std::strcmp(settings, "none") != 0) {
            std::filesystem::path file = settings ? std::filesystem::path(settings) : host::data_dir() / "settings.txt";
            std::vector<std::string> saved = host::settings_args(file);
            args.insert(args.end(), saved.begin(), saved.end());
            host::settings_use(file); // the menu's choices go back into it
        }
    }
    if (assets) {
        char image[1024];
        if (!port_find_image(assets, image, sizeof image)) {
            std::fprintf(stderr, "error: no disc image (.cue or .gdi) in %s\n", assets);
            return 4;
        }
        args.insert(args.end(), {"--disc", image});
    }
    for (int i = 1; i < argc; i++) args.emplace_back(argv[i]);
    std::vector<char *> list;
    for (std::string &a : args) list.push_back(a.data());
    list.push_back(nullptr);
    return port_main(static_cast<int>(args.size()), list.data());
}
