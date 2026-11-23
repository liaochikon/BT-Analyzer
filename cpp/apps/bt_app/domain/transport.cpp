#include "transport.hpp"

namespace btapp {

void start_or_pause(App& app, const bt::Player::Snapshot& transport) {
    if (app.player == nullptr) {
        return;
    }
    if (transport.playing) {
        app.player->pause();
        return;
    }
    if (transport.counting) {
        app.player->play();
        return;
    }
    if (app.count_in) {
        app.player->count_in(4);
        return;
    }
    app.player->play();
}

}  // namespace btapp
