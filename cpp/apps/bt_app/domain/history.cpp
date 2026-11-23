#include "history.hpp"

#include "monitor.hpp"
#include "notes.hpp"
#include "playback.hpp"

#include "bt/project.hpp"
#include "bt/session.hpp"
#include "imgui.h"

#include <algorithm>
#include <cstddef>
#include <filesystem>
#include <optional>
#include <system_error>
#include <utility>

namespace btapp {
namespace {

constexpr std::size_t kHistoryLimit = 100;

// Removes the folder a separation step keeps for its undo. Steps that can no longer be reached
// go through here: the redo side when a new step is added, the oldest step at the limit, and reset.
void discard(const ProjectStep& step) {
    if (step.kind != ProjectStep::Kind::Separation || step.held.empty()) {
        return;
    }
    std::error_code error;
    std::filesystem::remove_all(step.held, error);
}

// Mouse, keyboard focus, and drags are all idle. Playback values recorded now do not split a drag.
bool project_idle(const App& app) {
    return !app.note_gesture && !drag_active(app.loop_drag) && !drag_active(app.wave_drag) && !ImGui::IsAnyItemActive();
}

// Swaps the open folder with the held folder. Both sit in the model folder, so the swap goes
// through a spare name. A failure puts the folders back where they were.
bool swap_folders(const std::filesystem::path& current, const std::filesystem::path& held) {
    std::error_code error;
    const auto spare = current.parent_path() / L".swap";
    std::filesystem::remove_all(spare, error);
    std::filesystem::rename(current, spare, error);
    if (error) {
        return false;
    }
    std::filesystem::rename(held, current, error);
    if (error) {
        std::error_code restore;
        std::filesystem::rename(spare, current, restore);
        return false;
    }
    std::filesystem::rename(spare, held, error);
    if (error) {
        std::error_code restore;
        std::filesystem::rename(current, held, restore);
        std::filesystem::rename(spare, current, restore);
        return false;
    }
    return true;
}

// Renames the open folder and points the session at it. Does not record a step.
std::optional<bt::ProjectEntry> rename_folder(App& app, const std::string& name) {
    if (app.session == nullptr) {
        return std::nullopt;
    }
    const auto song = app.session->snapshot();
    if (song == nullptr || song->cache_directory.empty()) {
        return std::nullopt;
    }
    // Saved values must reach the old folder before its name changes.
    flush_project_playback(app);
    const auto old_directory = song->cache_directory;
    auto renamed = bt::rename_project(old_directory, name);
    if (!renamed) {
        return std::nullopt;
    }
    for (auto& item : app.projects) {
        if (item.directory == old_directory) {
            item = *renamed;
        }
    }
    app.session->relocate_cache(renamed->directory, renamed->name);
    return *renamed;
}

bool swap_open_project(App& app, const std::filesystem::path& held) {
    if (app.session == nullptr || held.empty()) {
        return false;
    }
    // A transcription still writing into the folder would land in the wrong copy.
    for (NoteStatus status : app.note_status) {
        if (status == NoteStatus::Running) {
            return false;
        }
    }
    const auto song = app.session->snapshot();
    if (song == nullptr || song->cache_directory.empty()) {
        return false;
    }
    flush_project_playback(app);
    const auto current = song->cache_directory;
    if (!swap_folders(current, held)) {
        return false;
    }
    // The folder path is unchanged, but its contents are not. Stop saving into it until the
    // reopened job reloads the playback file.
    app.playback_bound = false;
    expect_project_job(app, app.session->open_cache(current));
    return true;
}

// Applies the before side (undo) or the after side (redo) of one step.
bool apply_step(App& app, const ProjectStep& step, bool forward) {
    switch (step.kind) {
    case ProjectStep::Kind::Notes: {
        if (step.stem < 0 || step.stem >= bt::kStemCount) {
            return false;
        }
        const auto index = static_cast<std::size_t>(step.stem);
        app.notes[index] = forward ? step.notes_after : step.notes_before;
        commit_note_edit(app, step.stem);
        return true;
    }
    case ProjectStep::Kind::Playback:
        apply_project_playback(app, forward ? step.playback_after : step.playback_before);
        touch_project_playback(app);
        return true;
    case ProjectStep::Kind::Rename:
        return rename_folder(app, forward ? step.name_after : step.name_before).has_value();
    case ProjectStep::Kind::Separation:
        // Both directions swap the same two folders.
        return swap_open_project(app, step.held);
    }
    return false;
}

}  // namespace

void push_project_step(App& app, ProjectStep step) {
    for (const ProjectStep& redo : app.project_redo) {
        discard(redo);
    }
    app.project_redo.clear();
    app.project_undo.push_back(std::move(step));
    while (app.project_undo.size() > kHistoryLimit) {
        discard(app.project_undo.front());
        app.project_undo.erase(app.project_undo.begin());
    }
}

void reset_project_timeline(App& app) {
    for (const ProjectStep& step : app.project_undo) {
        discard(step);
    }
    for (const ProjectStep& step : app.project_redo) {
        discard(step);
    }
    app.project_undo.clear();
    app.project_redo.clear();
    app.separation_job = 0;
    for (std::size_t index = 0; index < app.rerun_pending.size(); ++index) {
        app.rerun_pending[index] = false;
        app.rerun_before[index].clear();
    }
}

void sync_project_timeline(App& app, const bt::SongSnapshot& song) {
    if (song.job != app.timeline_job) {
        // A job the app started (refresh, undo swap) keeps the steps. Any other job is a
        // different open or a close, so the line starts over.
        const bool started_here = song.job != 0 && song.job == app.timeline_expected_job;
        if (!started_here) {
            reset_project_timeline(app);
        }
        app.timeline_job = song.job;
    }
    if (app.separation_job == 0 || song.job != app.separation_job) {
        return;
    }
    if (song.phase == bt::SongPhase::Ready) {
        if (!song.replaced_directory.empty()) {
            ProjectStep step;
            step.kind = ProjectStep::Kind::Separation;
            step.held = song.replaced_directory;
            push_project_step(app, std::move(step));
        }
        app.separation_job = 0;
    } else if (song.phase == bt::SongPhase::Failed || song.phase == bt::SongPhase::Cancelled) {
        app.separation_job = 0;
    }
}

void record_project_playback(App& app) {
    if (!app.playback_bound || app.playback_loop_pending || !project_idle(app)) {
        return;
    }
    bt::ProjectPlayback current = capture_project_playback(app);
    if (same_project_playback(current, app.playback_committed)) {
        return;
    }
    ProjectStep step;
    step.kind = ProjectStep::Kind::Playback;
    step.playback_before = app.playback_committed;
    step.playback_after = current;
    app.playback_committed = current;
    push_project_step(app, std::move(step));
}

bool project_history_blocked(const App& app) {
    if (app.note_gesture || drag_active(app.loop_drag) || drag_active(app.wave_drag) || ImGui::IsAnyItemActive()) {
        return true;
    }
    if (app.separation_job != 0) {
        return true;
    }
    return std::any_of(app.rerun_pending.begin(), app.rerun_pending.end(), [](bool pending) { return pending; });
}

void expect_project_job(App& app, std::uint64_t job) {
    app.timeline_expected_job = job;
}

void start_project_separation(App& app, std::uint64_t job) {
    expect_project_job(app, job);
    app.separation_job = job;
}

bool rename_open_project(App& app, const std::string& name) {
    if (app.separation_job != 0 || app.session == nullptr) {
        return false;
    }
    const auto song = app.session->snapshot();
    if (song == nullptr || song->cache_directory.empty()) {
        return false;
    }
    const std::string before = song->project_name;
    auto renamed = rename_folder(app, name);
    if (!renamed) {
        return false;
    }
    ProjectStep step;
    step.kind = ProjectStep::Kind::Rename;
    step.name_before = before;
    step.name_after = renamed->name;
    push_project_step(app, std::move(step));
    return true;
}

void undo_project(App& app) {
    if (project_history_blocked(app)) {
        return;
    }
    // Pending mixer changes become a step first, so one Ctrl+Z reverts them.
    record_project_playback(app);
    if (app.project_undo.empty()) {
        return;
    }
    ProjectStep step = std::move(app.project_undo.back());
    app.project_undo.pop_back();
    if (!apply_step(app, step, false)) {
        app.project_undo.push_back(std::move(step));
        return;
    }
    app.project_redo.push_back(std::move(step));
    log_phrase(LogLevel::Info, "log.undo");
}

void redo_project(App& app) {
    if (project_history_blocked(app)) {
        return;
    }
    if (app.project_redo.empty()) {
        return;
    }
    ProjectStep step = std::move(app.project_redo.back());
    app.project_redo.pop_back();
    if (!apply_step(app, step, true)) {
        app.project_redo.push_back(std::move(step));
        return;
    }
    app.project_undo.push_back(std::move(step));
    log_phrase(LogLevel::Info, "log.redo");
}

}  // namespace btapp
