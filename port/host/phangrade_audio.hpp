#pragma once
// Copied into each port: only active when launched with PHANGRADE_SETTINGS.
#include <windows.h>
#include <mmdeviceapi.h>
#include <audiopolicy.h>
#include <algorithm>
#include <cmath>
#include <vector>

#ifdef _MSC_VER
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "uuid.lib")
#endif

namespace phangrade {
class Audio {
  std::vector<ISimpleAudioVolume *> sessions_;
  bool uninitialize_{};
  int applied_ = -1;

public:
  void open(int percent) {
    const auto result = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    uninitialize_ = SUCCEEDED(result);
    if (FAILED(result) && result != RPC_E_CHANGED_MODE) {
      return;
    }
    IMMDeviceEnumerator *enumerator = nullptr;
    if (FAILED(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL,
                                __uuidof(IMMDeviceEnumerator),
                                reinterpret_cast<void **>(&enumerator)))) {
      return;
    }
    IMMDeviceCollection *devices = nullptr;
    // Seed every active output before the game creates any streams, including
    // games that explicitly select an endpoint instead of using the default.
    if (SUCCEEDED(enumerator->EnumAudioEndpoints(eRender, DEVICE_STATE_ACTIVE, &devices))) {
      UINT count = 0;
      devices->GetCount(&count);
      for (UINT index = 0; index < count; ++index) {
        IMMDevice *device = nullptr;
        IAudioSessionManager *manager = nullptr;
        ISimpleAudioVolume *volume = nullptr;
        if (SUCCEEDED(devices->Item(index, &device))) {
          if (SUCCEEDED(device->Activate(__uuidof(IAudioSessionManager), CLSCTX_ALL, nullptr,
                                         reinterpret_cast<void **>(&manager)))) {
            if (SUCCEEDED(manager->GetSimpleAudioVolume(nullptr, FALSE, &volume))) {
              sessions_.push_back(volume);
            }
            manager->Release();
          }
          device->Release();
        }
      }
      devices->Release();
    }
    enumerator->Release();
    set(percent);
  }
  void set(int percent) {
    applied_ = std::clamp(percent, 0, 100);
    for (auto *session : sessions_) {
      session->SetMasterVolume(applied_ / 100.0f, nullptr);
      session->SetMute(FALSE, nullptr);
    }
  }
  int changed_volume() const {
    for (auto *session : sessions_) {
      float level = 0;
      BOOL muted = FALSE;
      if (SUCCEEDED(session->GetMasterVolume(&level)) && SUCCEEDED(session->GetMute(&muted))) {
        const int percent = muted ? 0 : static_cast<int>(std::lround(level * 100));
        if (percent != applied_) {
          return percent;
        }
      }
    }
    return applied_;
  }
  ~Audio() {
    for (auto *session : sessions_) {
      session->Release();
    }
    if (uninitialize_) {
      CoUninitialize();
    }
  }
};
} // namespace phangrade
