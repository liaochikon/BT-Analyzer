#pragma once

#include "app_state.hpp"

namespace btapp {

// App-wide files under the cache directory. They are not part of a song folder.
void set_language(App& app, int language);
void load_language(App& app);
void save_language(const App& app);
void load_performance(App& app);
void save_performance(const App& app);
void load_layout(App& app);
void save_layout(const App& app);
void load_output(App& app);
void save_output(const App& app);

}  // namespace btapp
