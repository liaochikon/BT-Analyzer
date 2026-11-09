#include "ui.hpp"

#include "bt/basic_pitch.hpp"
#include "bt/project.hpp"
#include "notes.hpp"
#include "beats.hpp"
#include "separate.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <cwctype>
#include <memory>
#include <stdexcept>
#include <string>

#include "imgui.h"
#include "imgui_impl_dx11.h"
#include "imgui_impl_win32.h"

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <d3d11.h>
#include <shellapi.h>

extern IMGUI_IMPL_API LRESULT ImGui_ImplWin32_WndProcHandler(HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam);

namespace {

struct Gfx {
    ID3D11Device* device = nullptr;
    ID3D11DeviceContext* context = nullptr;
    IDXGISwapChain* swap = nullptr;
    ID3D11RenderTargetView* target = nullptr;
    UINT width = 0;
    UINT height = 0;
    UINT swap_flags = 0;
    float dpi_scale = 1.f;
    float applied_scale = 0.f;
    bool in_resize = false;
    bool in_frame = false;
    bool imgui_ready = false;
    UINT pending_width = 0;
    UINT pending_height = 0;
    ImGuiStyle base_style{};
};

struct Host {
    btapp::App* app = nullptr;
    Gfx* gfx = nullptr;
    void (*render)(Host*) = nullptr;
};

bool create_target(Gfx& gfx) {
    if (gfx.swap == nullptr || gfx.device == nullptr) {
        return false;
    }
    ID3D11Texture2D* back = nullptr;
    if (FAILED(gfx.swap->GetBuffer(0, IID_PPV_ARGS(&back))) || back == nullptr) {
        return false;
    }
    const HRESULT created = gfx.device->CreateRenderTargetView(back, nullptr, &gfx.target);
    back->Release();
    return SUCCEEDED(created) && gfx.target != nullptr;
}

void resize(Gfx& gfx, UINT width, UINT height) {
    if (gfx.in_resize || gfx.swap == nullptr || gfx.context == nullptr || width == 0 || height == 0) {
        return;
    }
    if (width == gfx.width && height == gfx.height && gfx.target != nullptr) {
        return;
    }
    gfx.in_resize = true;
    gfx.context->OMSetRenderTargets(0, nullptr, nullptr);
    gfx.context->Flush();
    if (gfx.target != nullptr) {
        gfx.target->Release();
        gfx.target = nullptr;
    }
    const HRESULT resized = gfx.swap->ResizeBuffers(0, width, height, DXGI_FORMAT_UNKNOWN, gfx.swap_flags);
    if (SUCCEEDED(resized) && create_target(gfx)) {
        gfx.width = width;
        gfx.height = height;
    }
    gfx.in_resize = false;
}

void apply_dpi(Gfx& gfx) {
    if (!gfx.imgui_ready || std::fabs(gfx.dpi_scale - gfx.applied_scale) < 0.01f) {
        return;
    }
    const float scale = gfx.dpi_scale < 0.5f ? 1.f : gfx.dpi_scale;
    ImGui::GetStyle() = gfx.base_style;
    ImGui::GetStyle().ScaleAllSizes(scale);
    ImGui::GetIO().FontGlobalScale = scale;
    gfx.applied_scale = scale;
}

void render_frame(Host* host) {
    if (host == nullptr || host->gfx == nullptr || host->app == nullptr) {
        return;
    }
    Gfx& gfx = *host->gfx;
    if (gfx.pending_width != 0 && gfx.pending_height != 0 && !gfx.in_frame) {
        resize(gfx, gfx.pending_width, gfx.pending_height);
    }
    if (!gfx.imgui_ready || gfx.in_frame || gfx.in_resize || gfx.target == nullptr || gfx.context == nullptr) {
        return;
    }
    gfx.in_frame = true;
    apply_dpi(gfx);
    ImGui_ImplDX11_NewFrame();
    ImGui_ImplWin32_NewFrame();
    ImGui::NewFrame();
    btapp::draw(*host->app);
    ImGui::Render();
    const float clear[4] = {0.09f, 0.10f, 0.12f, 1.f};
    gfx.context->OMSetRenderTargets(1, &gfx.target, nullptr);
    gfx.context->ClearRenderTargetView(gfx.target, clear);
    ImGui_ImplDX11_RenderDrawData(ImGui::GetDrawData());
    gfx.swap->Present(1, 0);
    gfx.in_frame = false;
}

std::filesystem::path executable_directory() {
    std::wstring buffer(32768, L'\0');
    const DWORD length = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    buffer.resize(length);
    return std::filesystem::path(buffer).parent_path();
}

std::filesystem::path find_up(const std::filesystem::path& start, const std::filesystem::path& relative) {
    std::filesystem::path cursor = start;
    for (int step = 0; step < 8 && !cursor.empty(); ++step) {
        const auto candidate = cursor / relative;
        if (std::filesystem::exists(candidate)) {
            return candidate;
        }
        const auto parent = cursor.parent_path();
        if (parent == cursor) {
            break;
        }
        cursor = parent;
    }
    return {};
}

std::filesystem::path locate_asset(const std::filesystem::path& exe_dir, const std::filesystem::path& cwd, const char* relative) {
    if (auto found = find_up(exe_dir, relative); !found.empty()) {
        return found;
    }
    return find_up(cwd, relative);
}

std::filesystem::path startup_song(PWSTR command_line) {
    if (command_line == nullptr || command_line[0] == L'\0') {
        return {};
    }
    std::wstring text = command_line;
    while (!text.empty() && iswspace(text.front())) {
        text.erase(text.begin());
    }
    if (text.empty()) {
        return {};
    }
    if (text.front() == L'"') {
        const auto end = text.find(L'"', 1);
        text = text.substr(1, end == std::wstring::npos ? std::wstring::npos : end - 1);
    } else {
        const auto end = text.find_first_of(L" \t");
        if (end != std::wstring::npos) {
            text.resize(end);
        }
    }
    if (text.empty() || !std::filesystem::exists(text)) {
        return {};
    }
    return text;
}

LRESULT CALLBACK window_proc(HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam) {
    if (ImGui_ImplWin32_WndProcHandler(hwnd, msg, wparam, lparam)) {
        return true;
    }
    auto* host = reinterpret_cast<Host*>(GetWindowLongPtrW(hwnd, GWLP_USERDATA));
    switch (msg) {
    case WM_DPICHANGED:
        if (host != nullptr && host->gfx != nullptr) {
            host->gfx->dpi_scale = static_cast<float>(HIWORD(wparam)) / 96.f;
        }
        if (auto* suggested = reinterpret_cast<RECT*>(lparam)) {
            SetWindowPos(
                hwnd, nullptr, suggested->left, suggested->top, suggested->right - suggested->left,
                suggested->bottom - suggested->top, SWP_NOZORDER | SWP_NOACTIVATE);
        }
        return 0;
    case WM_SIZE:
        if (host != nullptr && host->gfx != nullptr && wparam != SIZE_MINIMIZED) {
            host->gfx->pending_width = LOWORD(lparam);
            host->gfx->pending_height = HIWORD(lparam);
            if (!host->gfx->in_frame) {
                resize(*host->gfx, host->gfx->pending_width, host->gfx->pending_height);
                if (host->render != nullptr) {
                    host->render(host);
                }
            }
        }
        return 0;
    case WM_DROPFILES:
        if (host != nullptr && host->app != nullptr && host->app->session != nullptr) {
            auto* drop = reinterpret_cast<HDROP>(wparam);
            wchar_t path[32768];
            if (DragQueryFileW(drop, 0, path, 32768) > 0 && !btapp::interface_locked(*host->app)) {
                host->app->session->open_song(path);
            }
            DragFinish(drop);
        }
        return 0;
    case WM_GETMINMAXINFO: {
        auto* info = reinterpret_cast<MINMAXINFO*>(lparam);
        const float scale = host != nullptr && host->gfx != nullptr ? host->gfx->dpi_scale : 1.f;
        info->ptMinTrackSize.x = static_cast<LONG>(960.f * scale);
        info->ptMinTrackSize.y = static_cast<LONG>(640.f * scale);
        return 0;
    }
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;
    default:
        break;
    }
    return DefWindowProcW(hwnd, msg, wparam, lparam);
}

std::wstring application_title() {
#ifndef BT_VERSION
#error BT_VERSION is missing
#endif
    std::wstring title = L"BT-Analyzer ";
    for (const char* cursor = BT_VERSION; *cursor != '\0'; ++cursor) {
        title.push_back(static_cast<unsigned char>(*cursor));
    }
    return title;
}

}  // namespace

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR command_line, int show) {
    ImGui_ImplWin32_EnableDpiAwareness();
    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);

    const POINT origin{80, 60};
    const HMONITOR monitor = MonitorFromPoint(origin, MONITOR_DEFAULTTOPRIMARY);
    const float dpi_scale = std::max(ImGui_ImplWin32_GetDpiScaleForMonitor(monitor), 1.f);
    MONITORINFO monitor_info{sizeof(monitor_info)};
    RECT work{0, 0, 1600, 900};
    if (GetMonitorInfoW(monitor, &monitor_info)) {
        work = monitor_info.rcWork;
    }
    const int max_width = std::max(640, static_cast<int>(work.right - work.left - 48));
    const int max_height = std::max(480, static_cast<int>(work.bottom - work.top - 48));
    const int window_width = std::min(std::max(static_cast<int>(1280.f * dpi_scale), 960), max_width);
    const int window_height = std::min(std::max(static_cast<int>(800.f * dpi_scale), 640), max_height);

    WNDCLASSEXW window_class{};
    window_class.cbSize = sizeof(window_class);
    window_class.style = CS_CLASSDC | CS_HREDRAW | CS_VREDRAW;
    window_class.lpfnWndProc = window_proc;
    window_class.hInstance = instance;
    window_class.lpszClassName = L"bt_analyzer";
    RegisterClassExW(&window_class);
    const std::wstring title = application_title();
    HWND hwnd = CreateWindowExW(
        WS_EX_ACCEPTFILES, window_class.lpszClassName, title.c_str(), WS_OVERLAPPEDWINDOW, origin.x, origin.y, window_width,
        window_height, nullptr, nullptr, instance, nullptr);

    const auto exe_dir = executable_directory();
    const auto cwd = std::filesystem::current_path();
    const auto font_path = locate_asset(exe_dir, cwd, "fonts/sarasa-gothic-tc-nerd-merged.ttf");

    const auto cache_directory = exe_dir / "cache";
    const auto saves_directory = exe_dir / "saves";
    btapp::App boot;
    boot.cache_directory = cache_directory;
    btapp::load_performance(boot);
    const int demucs_threads = boot.demucs_threads;
    const int note_workers = boot.note_workers;
    const std::string separation_model = boot.separation_model;
    const int separation_preference = boot.separation_preference;

    // Full graph optimization fails once the swap chain has reserved address space.
    auto engine = std::make_unique<btapp::StemEngine>();
    engine->discover(exe_dir, cwd);
    const auto preference = separation_preference == 1
        ? btapp::InferenceDevice::Cpu
        : separation_preference == 2 ? btapp::InferenceDevice::Cuda : btapp::InferenceDevice::Auto;
    engine->apply(separation_model, preference, demucs_threads);
    const std::string model_error = engine->error();
    const int separation_chunk = engine->chunk();
    const std::string separation_device = engine->device();
    const auto pitch_path = locate_asset(exe_dir, cwd, "models/nmp.onnx");
    std::string pitch_error;
    std::unique_ptr<btapp::NoteService> note_service;
    if (pitch_path.empty()) {
        pitch_error = "找不到 models/nmp.onnx";
    } else {
        try {
            note_service = std::make_unique<btapp::NoteService>(pitch_path, note_workers);
        } catch (const std::exception& ex) {
            pitch_error = ex.what();
            note_service.reset();
        }
    }
    const bool pitch_ready = note_service != nullptr;

    Gfx gfx;
    gfx.dpi_scale = dpi_scale;
    DXGI_SWAP_CHAIN_DESC swap_desc{};
    swap_desc.BufferCount = 2;
    swap_desc.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    swap_desc.BufferDesc.RefreshRate.Numerator = 60;
    swap_desc.BufferDesc.RefreshRate.Denominator = 1;
    swap_desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    swap_desc.OutputWindow = hwnd;
    swap_desc.SampleDesc.Count = 1;
    swap_desc.Windowed = TRUE;
    swap_desc.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD;
    swap_desc.Flags = DXGI_SWAP_CHAIN_FLAG_ALLOW_MODE_SWITCH;
    D3D_FEATURE_LEVEL feature = D3D_FEATURE_LEVEL_11_0;
    HRESULT created = D3D11CreateDeviceAndSwapChain(
        nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, &feature, 1, D3D11_SDK_VERSION, &swap_desc, &gfx.swap,
        &gfx.device, nullptr, &gfx.context);
    if (FAILED(created)) {
        swap_desc.BufferCount = 1;
        swap_desc.SwapEffect = DXGI_SWAP_EFFECT_DISCARD;
        swap_desc.Flags = 0;
        created = D3D11CreateDeviceAndSwapChain(
            nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, &feature, 1, D3D11_SDK_VERSION, &swap_desc, &gfx.swap,
            &gfx.device, nullptr, &gfx.context);
    }
    if (FAILED(created)) {
        MessageBoxW(nullptr, L"Direct3D 11 無法啟動", title.c_str(), MB_ICONERROR);
        return 1;
    }
    gfx.swap_flags = swap_desc.Flags;
    if (!create_target(gfx)) {
        MessageBoxW(nullptr, L"Direct3D 11 無法啟動", title.c_str(), MB_ICONERROR);
        return 1;
    }
    RECT client{};
    GetClientRect(hwnd, &client);
    gfx.width = static_cast<UINT>(client.right - client.left);
    gfx.height = static_cast<UINT>(client.bottom - client.top);

    bt::SessionOptions options;
    options.cache_directory = saves_directory;
    options.model_key = engine->model_key();
    options.backend_key = [eng = engine.get()] { return eng->model_key(); };
    options.backend_device = [eng = engine.get()] { return eng->device(); };
    options.separate_mix = [eng = engine.get()](
                               const bt::Audio& audio, const std::function<void(float)>& progress,
                               const std::function<bool()>& cancelled) -> std::expected<bt::Stems, std::string> {
        return eng->separate(audio, progress, cancelled);
    };
    auto session_created = bt::Session::create(std::move(options));
    if (!session_created) {
        MessageBoxW(nullptr, L"Session 無法啟動", title.c_str(), MB_ICONERROR);
        return 1;
    }
    auto session = std::move(*session_created);
    bt::Player player;
    if (auto started = player.start_device(); !started) {
        // The window still opens. Playback controls report the device failure.
    }

    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImGuiIO& io = ImGui::GetIO();
    io.IniFilename = nullptr;
    io.FontAllowUserScaling = false;
    ImGui::StyleColorsDark();
    ImGui::GetStyle().WindowRounding = 0.f;
    ImGui::GetStyle().ChildRounding = 6.f;
    ImGui::GetStyle().FrameRounding = 4.f;
    ImGui::GetStyle().DisabledAlpha = 0.68f;
    gfx.base_style = ImGui::GetStyle();
    if (!font_path.empty()) {
        auto bytes = btapp::read_binary(font_path);
        if (!bytes.empty()) {
            void* owned = ImGui::MemAlloc(bytes.size());
            std::memcpy(owned, bytes.data(), bytes.size());
            ImFontConfig config;
            config.OversampleH = 1;
            config.OversampleV = 1;
            config.PixelSnapH = true;
            io.Fonts->TexDesiredWidth = 4096;
            if (io.Fonts->AddFontFromMemoryTTF(
                    owned, static_cast<int>(bytes.size()), 18.f, &config, io.Fonts->GetGlyphRangesChineseFull()) ==
                nullptr) {
                ImGui::MemFree(owned);
            }
        }
    }

    ImGui_ImplWin32_Init(hwnd);
    ImGui_ImplDX11_Init(gfx.device, gfx.context);
    gfx.imgui_ready = true;
    apply_dpi(gfx);

    btapp::App app;
    app.session = session.get();
    app.player = &player;
    app.hwnd = reinterpret_cast<HWND__*>(hwnd);
    app.cache_directory = cache_directory;
    app.saves_directory = saves_directory;
    app.note_workers = note_workers;
    app.demucs_threads = demucs_threads;
    app.separation_model = engine->model_key();
    app.separation_preference = separation_preference;
    app.engine = engine.get();
    app.device_note = player.device_error();
    btapp::load_output(app);
    app.model_note = model_error;
    app.separation_chunk = separation_chunk;
    app.separation_device = separation_device;
    app.pitch_note = pitch_error;
    app.pitch_ready = pitch_ready;
    if (!pitch_path.empty()) {
        app.pitch_model_path = pitch_path;
    }
    std::unique_ptr<btapp::BeatService> beat_service = std::make_unique<btapp::BeatService>();
    app.beat_service = beat_service.get();
    if (note_service) {
        app.note_service = note_service.get();
    }
    Host host{&app, &gfx, &render_frame};
    SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(&host));
    DragAcceptFiles(hwnd, TRUE);
    ShowWindow(hwnd, show);
    UpdateWindow(hwnd);

    const auto song = startup_song(command_line);
    if (!song.empty()) {
        session->open_song(song);
    }

    bool running = true;
    while (running) {
        MSG message;
        while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) {
            TranslateMessage(&message);
            DispatchMessageW(&message);
            if (message.message == WM_QUIT) {
                running = false;
            }
        }
        if (!running) {
            break;
        }
        if (gfx.target == nullptr) {
            GetClientRect(hwnd, &client);
            resize(gfx, static_cast<UINT>(client.right - client.left), static_cast<UINT>(client.bottom - client.top));
        }
        render_frame(&host);
    }

    ImGui_ImplDX11_Shutdown();
    ImGui_ImplWin32_Shutdown();
    ImGui::DestroyContext();
    if (gfx.target != nullptr) {
        gfx.target->Release();
    }
    if (gfx.swap != nullptr) {
        gfx.swap->Release();
    }
    if (gfx.context != nullptr) {
        gfx.context->Release();
    }
    if (gfx.device != nullptr) {
        gfx.device->Release();
    }
    DestroyWindow(hwnd);
    UnregisterClassW(window_class.lpszClassName, instance);
    CoUninitialize();
    return 0;
}
