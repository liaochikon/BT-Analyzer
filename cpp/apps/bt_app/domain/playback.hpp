#pragma once

#include "app_state.hpp"

#include <memory>

namespace btapp {

// Mixer and player values that belong to the open song. Switching songs saves one
// folder and loads the other. A missing file uses the defaults in ProjectPlayback.
void touch_project_playback(App& app);
void flush_project_playback(App& app);
void sync_project_playback(App& app, const bt::SongSnapshot& song);
void settle_project_loop(App& app, const bt::SongSnapshot& song);
void push_player_mix(App& app);

// Keep audible / selected / in-flight stems in RAM; unload the rest from the session snapshot.
void sync_stem_residency(App& app, const bt::SongSnapshot& song);
// Block until `stem` audio is resident (for transcription). Returns an updated snapshot.
std::shared_ptr<const bt::SongSnapshot> ensure_stem_resident(App& app, int stem);

// Current values of the song's playback settings, as they would be saved.
bt::ProjectPlayback capture_project_playback(const App& app);
// Applies the settings to the session, the player and the UI. Sets the committed baseline.
void apply_project_playback(App& app, const bt::ProjectPlayback& playback);
bool same_project_playback(const bt::ProjectPlayback& left, const bt::ProjectPlayback& right);

}  // namespace btapp
