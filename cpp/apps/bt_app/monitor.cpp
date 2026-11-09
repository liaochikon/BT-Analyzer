#include "monitor.hpp"

#include "ui.hpp"

#include "bt/contract.hpp"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <deque>
#include <fstream>
#include <mutex>
#include <string_view>

#include "imgui.h"
#include "imgui_internal.h"

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <psapi.h>

namespace btapp {
namespace {

struct Line {
    LogLevel level = LogLevel::Info;
    std::string text;
};

struct CpuMeter {
    unsigned long long idle = 0;
    unsigned long long kernel = 0;
    unsigned long long user = 0;
    unsigned long long process_kernel = 0;
    unsigned long long process_user = 0;
    bool have = false;
    float system = 0.f;
    float process = 0.f;
};

std::mutex gate;
std::deque<Line> lines;
std::filesystem::path log_path;
bool file_capped = false;
CpuMeter cpu;
int processors = 0;
std::string computer;
std::uint64_t seen_job = 0;
bt::SongPhase seen_phase = bt::SongPhase::Empty;
std::string seen_detail;
bool seen_ready = false;

const char* pick(int language, const char* hant, const char* hans, const char* en) {
    if (language == 2) {
        return en;
    }
    if (language == 1) {
        return hans;
    }
    return hant;
}

const char* stem_name(int stem, int language) {
    static const char* hant[] = {"鼓", "貝斯", "其他", "人聲", "吉他", "鋼琴"};
    static const char* hans[] = {"鼓", "贝斯", "其他", "人声", "吉他", "钢琴"};
    static const char* en[] = {"Drums", "Bass", "Other", "Vocals", "Guitar", "Piano"};
    if (stem < 0 || stem >= bt::kStemCount) {
        return "";
    }
    return pick(language, hant[stem], hans[stem], en[stem]);
}

std::string utf8_from_wide(std::wstring_view wide) {
    if (wide.empty()) {
        return {};
    }
    const int size = WideCharToMultiByte(CP_UTF8, 0, wide.data(), static_cast<int>(wide.size()), nullptr, 0, nullptr, nullptr);
    if (size <= 0) {
        return {};
    }
    std::string utf8(static_cast<std::size_t>(size), '\0');
    WideCharToMultiByte(CP_UTF8, 0, wide.data(), static_cast<int>(wide.size()), utf8.data(), size, nullptr, nullptr);
    return utf8;
}

std::string utf8_from_path(const std::filesystem::path& path) {
    return utf8_from_wide(path.wstring());
}

unsigned long long filetime_of(const FILETIME& time) {
    ULARGE_INTEGER value{};
    value.LowPart = time.dwLowDateTime;
    value.HighPart = time.dwHighDateTime;
    return value.QuadPart;
}

std::string clock_text() {
    SYSTEMTIME time{};
    GetLocalTime(&time);
    char buffer[16];
    std::snprintf(buffer, sizeof buffer, "%02u:%02u:%02u", time.wHour, time.wMinute, time.wSecond);
    return buffer;
}

void remember(LogLevel level, std::string text) {
    lines.push_back(Line{level, std::move(text)});
    while (lines.size() > 800) {
        lines.pop_front();
    }
}

void append_file(const std::string& text) {
    if (log_path.empty() || file_capped) {
        return;
    }
    std::error_code error;
    const auto size = std::filesystem::file_size(log_path, error);
    if (!error && size > 4ull * 1024ull * 1024ull) {
        file_capped = true;
        remember(LogLevel::Warn, std::string(clock_text()) + "  log file is full");
        return;
    }
    std::ofstream output(log_path, std::ios::binary | std::ios::app);
    if (!output) {
        return;
    }
    output << text << '\n';
}

void sample_cpu() {
    FILETIME idle{};
    FILETIME kernel{};
    FILETIME user{};
    FILETIME created{};
    FILETIME exited{};
    FILETIME process_kernel{};
    FILETIME process_user{};
    if (GetSystemTimes(&idle, &kernel, &user) == 0 ||
        GetProcessTimes(GetCurrentProcess(), &created, &exited, &process_kernel, &process_user) == 0) {
        return;
    }
    const unsigned long long next_idle = filetime_of(idle);
    const unsigned long long next_kernel = filetime_of(kernel);
    const unsigned long long next_user = filetime_of(user);
    const unsigned long long next_process_kernel = filetime_of(process_kernel);
    const unsigned long long next_process_user = filetime_of(process_user);
    if (cpu.have) {
        const unsigned long long system_delta = (next_kernel - cpu.kernel) + (next_user - cpu.user);
        const unsigned long long idle_delta = next_idle - cpu.idle;
        const unsigned long long process_delta = (next_process_kernel - cpu.process_kernel) + (next_process_user - cpu.process_user);
        if (system_delta > 0) {
            const float system = std::clamp(static_cast<float>(system_delta - idle_delta) / static_cast<float>(system_delta), 0.f, 1.f);
            const float process = std::clamp(static_cast<float>(process_delta) / static_cast<float>(system_delta), 0.f, 1.f);
            cpu.system += (system - cpu.system) * 0.35f;
            cpu.process += (process - cpu.process) * 0.35f;
        }
    }
    cpu.idle = next_idle;
    cpu.kernel = next_kernel;
    cpu.user = next_user;
    cpu.process_kernel = next_process_kernel;
    cpu.process_user = next_process_user;
    cpu.have = true;
}

void ensure_machine() {
    if (processors > 0) {
        return;
    }
    SYSTEM_INFO info{};
    GetSystemInfo(&info);
    processors = static_cast<int>(info.dwNumberOfProcessors);
    wchar_t name[256] = {};
    DWORD count = 256;
    if (GetComputerNameW(name, &count) != 0) {
        computer = utf8_from_wide(std::wstring_view(name, count));
    }
}

std::string bytes_text(unsigned long long bytes) {
    const double megabytes = static_cast<double>(bytes) / (1024.0 * 1024.0);
    char buffer[32];
    if (megabytes >= 1024.0) {
        std::snprintf(buffer, sizeof buffer, "%.1f GB", megabytes / 1024.0);
    } else {
        std::snprintf(buffer, sizeof buffer, "%.0f MB", megabytes);
    }
    return buffer;
}

void meter(float fraction, const char* overlay) {
    const ImVec4 color = fraction >= 0.9f ? ImVec4(0.773f, 0.361f, 0.333f, 1.f)
                         : fraction >= 0.7f ? ImVec4(0.804f, 0.725f, 0.388f, 1.f)
                                            : ImVec4(0.329f, 0.710f, 0.349f, 1.f);
    ImGui::PushStyleColor(ImGuiCol_PlotHistogram, color);
    ImGui::ProgressBar(std::clamp(fraction, 0.f, 1.f), ImVec2(-1.f, 0.f), overlay);
    ImGui::PopStyleColor();
}

const char* note_state_text(NoteStatus status, int language) {
    switch (status) {
    case NoteStatus::Running:
        return pick(language, "轉譜中", "转谱中", "Running");
    case NoteStatus::Ready:
        return pick(language, "完成", "完成", "Ready");
    case NoteStatus::Failed:
        return pick(language, "失敗", "失败", "Failed");
    case NoteStatus::Idle:
        return pick(language, "閒置", "闲置", "Idle");
    }
    return "";
}

ImVec4 note_state_color(NoteStatus status) {
    switch (status) {
    case NoteStatus::Running:
        return ImVec4(0.804f, 0.725f, 0.388f, 1.f);
    case NoteStatus::Ready:
        return ImVec4(0.329f, 0.710f, 0.349f, 1.f);
    case NoteStatus::Failed:
        return ImVec4(0.773f, 0.361f, 0.333f, 1.f);
    case NoteStatus::Idle:
        return ImVec4(0.62f, 0.62f, 0.62f, 1.f);
    }
    return ImVec4(1.f, 1.f, 1.f, 1.f);
}

const char* phase_label(bt::SongPhase phase, bool from_cache, int language) {
    switch (phase) {
    case bt::SongPhase::Empty:
        return pick(language, "沒有歌曲", "没有歌曲", "No song");
    case bt::SongPhase::Decoding:
        return pick(language, "解碼中", "解码中", "Decoding");
    case bt::SongPhase::Separating:
        return pick(language, "分離中", "分离中", "Separating");
    case bt::SongPhase::Ready:
        return from_cache ? pick(language, "就緒 · 快取", "就绪 · 缓存", "Ready · cache") : pick(language, "就緒", "就绪", "Ready");
    case bt::SongPhase::Cancelled:
        return pick(language, "已取消", "已取消", "Cancelled");
    case bt::SongPhase::Failed:
        return pick(language, "失敗", "失败", "Failed");
    }
    return "";
}

void log_phase(int language, const bt::SongSnapshot& song) {
    if (!seen_ready) {
        seen_ready = true;
        seen_job = song.job;
        seen_phase = song.phase;
        seen_detail = song.detail;
        if (song.phase == bt::SongPhase::Empty) {
            return;
        }
    } else if (seen_job == song.job && seen_phase == song.phase && seen_detail == song.detail) {
        return;
    }
    seen_job = song.job;
    seen_phase = song.phase;
    seen_detail = song.detail;
    const std::string name = song.project_name.empty() ? utf8_from_path(song.source.filename()) : song.project_name;
    std::string tail = name.empty() ? std::string{} : " · " + name;
    switch (song.phase) {
    case bt::SongPhase::Decoding:
        log_phrase(LogLevel::Info, language, "開啟歌曲", "打开歌曲", "Open song", tail);
        break;
    case bt::SongPhase::Separating:
        log_phrase(LogLevel::Info, language, "開始分離", "开始分离", "Separating", tail);
        break;
    case bt::SongPhase::Ready:
        log_phrase(
            LogLevel::Info, language, song.from_cache ? "歌曲就緒 · 快取" : "歌曲就緒", song.from_cache ? "歌曲就绪 · 缓存" : "歌曲就绪",
            song.from_cache ? "Song ready · cache" : "Song ready", tail);
        break;
    case bt::SongPhase::Cancelled:
        log_phrase(LogLevel::Warn, language, "分離已取消", "分离已取消", "Separation cancelled", tail);
        break;
    case bt::SongPhase::Failed:
        log_phrase(LogLevel::Error, language, "歌曲失敗", "歌曲失败", "Song failed", song.detail.empty() ? tail : " · " + song.detail);
        break;
    case bt::SongPhase::Empty:
        log_phrase(LogLevel::Info, language, "已關閉歌曲", "已关闭歌曲", "Song closed");
        break;
    }
}

void draw_machine(int language) {
    ensure_machine();
    sample_cpu();
    ImGui::SeparatorText(pick(language, "本機", "本机", "Machine"));
    if (!computer.empty()) {
        ImGui::TextUnformatted(computer.c_str());
    }
    char overlay[64];
    std::snprintf(overlay, sizeof overlay, "%s  %.0f%%", pick(language, "程序", "进程", "App"), cpu.process * 100.f);
    meter(cpu.process, overlay);
    std::snprintf(overlay, sizeof overlay, "%s  %.0f%%", pick(language, "系統", "系统", "System"), cpu.system * 100.f);
    meter(cpu.system, overlay);

    MEMORYSTATUSEX memory{};
    memory.dwLength = sizeof memory;
    unsigned long long used = 0;
    unsigned long long total = 0;
    if (GlobalMemoryStatusEx(&memory) != 0) {
        total = memory.ullTotalPhys;
        used = memory.ullTotalPhys - memory.ullAvailPhys;
    }
    PROCESS_MEMORY_COUNTERS_EX process{};
    process.cb = sizeof process;
    unsigned long long working = 0;
    if (GetProcessMemoryInfo(GetCurrentProcess(), reinterpret_cast<PROCESS_MEMORY_COUNTERS*>(&process), sizeof process) != 0) {
        working = process.WorkingSetSize;
    }
    const float ram = total > 0 ? static_cast<float>(static_cast<double>(used) / static_cast<double>(total)) : 0.f;
    std::snprintf(overlay, sizeof overlay, "%s %s / %s", pick(language, "記憶體", "内存", "Memory"), bytes_text(used).c_str(), bytes_text(total).c_str());
    meter(ram, overlay);
    ImGui::TextDisabled(
        "%s %s    %s %d    %.0f fps", pick(language, "程序", "进程", "App"), bytes_text(working).c_str(),
        pick(language, "核心", "核心", "Cores"), processors, static_cast<double>(ImGui::GetIO().Framerate));
}

void draw_song(const App& app, const bt::SongSnapshot& song, const bt::Player::Snapshot& transport) {
    const int language = app.language;
    ImGui::SeparatorText(pick(language, "歌曲", "歌曲", "Song"));
    const std::string name = song.project_name.empty() ? utf8_from_path(song.source.filename()) : song.project_name;
    ImGui::TextUnformatted(name.empty() ? pick(language, "沒有歌曲", "没有歌曲", "No song") : name.c_str());
    ImGui::TextDisabled("%s", phase_label(song.phase, song.from_cache, language));
    if (song.phase == bt::SongPhase::Decoding || song.phase == bt::SongPhase::Separating) {
        const float fraction = song.phase == bt::SongPhase::Separating ? song.progress : 0.f;
        char overlay[64];
        std::snprintf(
            overlay, sizeof overlay, "%s  %.0f%%", phase_label(song.phase, song.from_cache, language),
            std::clamp(fraction, 0.f, 1.f) * 100.f);
        ProgressBar("separate", fraction).overlay(overlay).draw();
    }
    const int position = static_cast<int>(transport.position);
    const int length = static_cast<int>(transport.length);
    ImGui::TextDisabled(
        "%s    %d:%02d / %d:%02d    %.1fx", transport.playing ? pick(language, "播放", "播放", "Playing") : pick(language, "停止", "停止", "Stopped"),
        position / 60, position % 60, length / 60, length % 60, transport.speed);
}

void draw_jobs(const App& app) {
    const int language = app.language;
    ImGui::SeparatorText(pick(language, "工作", "工作", "Jobs"));
    bool notes_running = false;
    if (ImGui::BeginTable("jobs", 2, ImGuiTableFlags_SizingStretchProp)) {
        for (int stem = 0; stem < bt::kStemCount; ++stem) {
            const auto status = app.note_status[static_cast<std::size_t>(stem)];
            notes_running = notes_running || status == NoteStatus::Running;
            ImGui::TableNextRow();
            ImGui::TableSetColumnIndex(0);
            ImGui::TextUnformatted(stem_name(stem, language));
            ImGui::TableSetColumnIndex(1);
            ImGui::TextColored(note_state_color(status), "%s", note_state_text(status, language));
            if (status == NoteStatus::Failed && !app.note_detail[static_cast<std::size_t>(stem)].empty()) {
                ImGui::SameLine();
                ImGui::TextDisabled("%s", app.note_detail[static_cast<std::size_t>(stem)].c_str());
            }
        }
        ImGui::EndTable();
    }
    if (notes_running) {
        char overlay[64];
        std::snprintf(
            overlay, sizeof overlay, "%s  %.0f%%", pick(language, "轉譜中", "转谱中", "Transcribing"),
            std::clamp(app.note_progress, 0.f, 1.f) * 100.f);
        ProgressBar("notes", app.note_progress).overlay(overlay).draw();
    }
    const char* beat = pick(language, "閒置", "闲置", "Idle");
    if (app.beat_pending) {
        beat = pick(language, "偵測中", "检测中", "Detecting");
    } else if (!app.beat_detail.empty()) {
        beat = app.beat_detail.c_str();
    } else if (app.beat_ticket != 0) {
        beat = pick(language, "完成", "完成", "Ready");
    }
    ImGui::TextDisabled("%s", pick(language, "拍點", "拍点", "Beats"));
    ImGui::SameLine();
    if (app.beat_pending) {
        ImGui::TextColored(ImVec4(0.804f, 0.725f, 0.388f, 1.f), "%s", beat);
    } else if (!app.beat_detail.empty()) {
        ImGui::TextColored(ImVec4(0.773f, 0.361f, 0.333f, 1.f), "%s", beat);
    } else {
        ImGui::TextUnformatted(beat);
        if (app.beat_ticket != 0 && app.beat_times != nullptr) {
            ImGui::SameLine();
            ImGui::TextDisabled("%zu", app.beat_times->size());
        }
    }
    if (!app.device_note.empty()) {
        ImGui::TextColored(ImVec4(0.804f, 0.725f, 0.388f, 1.f), "%s", app.device_note.c_str());
    }
    if (!app.model_note.empty()) {
        ImGui::TextColored(ImVec4(0.804f, 0.725f, 0.388f, 1.f), "%s", app.model_note.c_str());
    }
    if (!app.pitch_ready) {
        const char* pitch = app.pitch_note.empty() ? "models/nmp.onnx" : app.pitch_note.c_str();
        ImGui::TextColored(ImVec4(0.773f, 0.361f, 0.333f, 1.f), "%s", pitch);
    }
}

void draw_log(int language) {
    ImGui::SeparatorText(pick(language, "紀錄", "记录", "Log"));
    if (MediumButton("clear-log", Icon::Clear).tip(pick(language, "清除畫面", "清除画面", "Clear view")).draw()) {
        std::lock_guard lock(gate);
        lines.clear();
    }
    std::string text;
    {
        std::lock_guard lock(gate);
        for (const Line& line : lines) {
            text += line.text;
            text.push_back('\n');
        }
    }
    if (text.empty()) {
        text = " ";
    }
    const float footer = ImGui::GetTextLineHeightWithSpacing();
    ImGui::InputTextMultiline(
        "##monitor-log", text.data(), text.size() + 1, ImVec2(-1.f, -footer), ImGuiInputTextFlags_ReadOnly);
    const ImGuiID log_id = ImGui::GetItemID();
    ImGuiWindow* parent = ImGui::GetCurrentWindow();
    char child_name[384];
    std::snprintf(child_name, sizeof child_name, "%s/##monitor-log_%08X", parent->Name, log_id);
    if (ImGuiWindow* log_window = ImGui::FindWindowByName(child_name)) {
        const bool follow = log_window->Scroll.y >= log_window->ScrollMax.y - 8.f;
        if (follow) {
            log_window->Scroll.y = log_window->ScrollMax.y;
        }
    }
    if (ImGui::BeginPopupContextItem("monitor-log-menu")) {
        if (ImGui::MenuItem(pick(language, "複製全部", "复制全部", "Copy all"))) {
            ImGui::SetClipboardText(text.c_str());
        }
        ImGui::EndPopup();
    }
    const std::string path = utf8_from_path(log_path);
    ImGui::TextDisabled("%s", path.c_str());
}

}  // namespace

void monitor_start(const std::filesystem::path& cache_directory) {
    std::error_code error;
    std::filesystem::create_directories(cache_directory, error);
    std::lock_guard lock(gate);
    log_path = cache_directory / "bt-app.log";
}

void log_message(LogLevel level, std::string text) {
    const std::string stamped = clock_text() + "  " + text;
    std::lock_guard lock(gate);
    append_file(stamped);
    remember(level, stamped);
}

void log_phrase(LogLevel level, int language, const char* hant, const char* hans, const char* en, const std::string& tail) {
    log_message(level, std::string(pick(language, hant, hans, en)) + tail);
}

void log_stem(LogLevel level, int language, int stem, const char* hant, const char* hans, const char* en, const std::string& tail) {
    log_message(level, std::string(pick(language, hant, hans, en)) + " · " + stem_name(stem, language) + tail);
}

void log_startup(const App& app) {
    log_phrase(LogLevel::Info, app.language, "啟動", "启动", "Started");
    if (app.device_note.empty()) {
        log_phrase(LogLevel::Info, app.language, "播放裝置就緒", "播放设备就绪", "Playback device ready");
    } else {
        log_message(LogLevel::Warn, app.device_note);
    }
    if (app.model_note.empty()) {
        log_phrase(
            LogLevel::Info, app.language, "分離模型就緒", "分离模型就绪", "Separation model ready",
            app.separation_chunk > 0 ? " · " + std::to_string(app.separation_chunk) + " samples" : std::string{});
    } else {
        log_message(LogLevel::Warn, app.model_note);
    }
    if (app.pitch_ready) {
        log_phrase(LogLevel::Info, app.language, "轉譜模型就緒", "转谱模型就绪", "Transcription model ready");
    } else if (!app.pitch_note.empty()) {
        log_message(LogLevel::Warn, app.pitch_note);
    } else {
        log_phrase(LogLevel::Warn, app.language, "找不到 models/nmp.onnx", "找不到 models/nmp.onnx", "models/nmp.onnx was not found");
    }
    log_message(LogLevel::Info, utf8_from_path(log_path));
}

void draw_mono_progress(const char* id, float fraction, float width, float height, const char* overlay) {
    ProgressBar(id, fraction).size(width, height).overlay(overlay).draw();
}

void draw_monitor(App& app) {
    static bool started = false;
    if (!started && !app.cache_directory.empty()) {
        started = true;
        monitor_start(app.cache_directory);
        log_startup(app);
    }
    const auto song = app.session != nullptr ? app.session->snapshot() : nullptr;
    if (song != nullptr) {
        log_phase(app.language, *song);
    }
    if (!app.monitor_open) {
        return;
    }
    const auto transport = app.player != nullptr ? app.player->snapshot() : bt::Player::Snapshot{};
    const float scale = ImGui::GetIO().FontGlobalScale < 0.5f ? 1.f : ImGui::GetIO().FontGlobalScale;
    const ImGuiViewport* viewport = ImGui::GetMainViewport();
    ImGui::SetNextWindowSize(ImVec2(420.f * scale, 640.f * scale), ImGuiCond_Appearing);
    ImGui::SetNextWindowPos(
        ImVec2(viewport->WorkPos.x + viewport->WorkSize.x - 440.f * scale, viewport->WorkPos.y + 48.f * scale), ImGuiCond_Appearing);
    ImGui::SetNextWindowBgAlpha(0.96f);
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(12.f * scale, 10.f * scale));
    ImGui::PushStyleVar(ImGuiStyleVar_WindowRounding, 6.f * scale);
    const std::string title = std::string(pick(app.language, "系統監控", "系统监控", "Monitor")) + "###monitor";
    const bool open = ImGui::Begin(title.c_str(), &app.monitor_open);
    if (open) {
        draw_machine(app.language);
        if (song != nullptr) {
            draw_song(app, *song, transport);
        }
        draw_jobs(app);
        draw_log(app.language);
    }
    ImGui::End();
    ImGui::PopStyleVar(2);
}

}  // namespace btapp
