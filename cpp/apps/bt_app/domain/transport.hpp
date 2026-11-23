#pragma once

#include "app_state.hpp"

#include "bt/player.hpp"

namespace btapp {

void start_or_pause(App& app, const bt::Player::Snapshot& transport);

}  // namespace btapp
