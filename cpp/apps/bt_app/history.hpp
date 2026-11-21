#pragma once

#include "app_state.hpp"

#include <cstdint>
#include <string>

namespace btapp {

// One undo and redo line for the open project. Note edits, mixer and player values,
// renames, and separation reruns share it. Opening or closing a project starts a new line.

// Adds a step after the current position and drops the redo steps.
void push_project_step(App& app, ProjectStep step);
// Drops every step. Held separation folders are removed from disk.
void reset_project_timeline(App& app);
// Runs once per frame: starts a new line when the session moved to a job the app did not start,
// and records a finished separation rerun.
void sync_project_timeline(App& app, const bt::SongSnapshot& song);
// Records mixer, player, and loop changes once the pointer and keyboard are idle.
void record_project_playback(App& app);
// True while an undo or redo would race with a gesture, a drag, text input, or a rerun.
bool project_history_blocked(const App& app);
// Marks a session job that the app started, so the timeline keeps its steps.
void expect_project_job(App& app, std::uint64_t job);
// Starts a separation rerun on the open project and remembers its job.
void start_project_separation(App& app, std::uint64_t job);
// Renames the open project and records the step. The name is the display name.
bool rename_open_project(App& app, const std::string& name);
void undo_project(App& app);
void redo_project(App& app);

}  // namespace btapp
