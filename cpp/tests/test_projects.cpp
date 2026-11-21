#include "bt/project.hpp"

#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include <filesystem>
#include <fstream>
#include <string>

namespace {

struct TempDir {
    std::filesystem::path path;

    explicit TempDir(const std::string& name) {
        path = std::filesystem::temp_directory_path() / name;
        std::filesystem::remove_all(path);
        std::filesystem::create_directories(path);
    }

    ~TempDir() {
        std::error_code error;
        std::filesystem::remove_all(path, error);
    }
};

std::string utf8_filename(const std::filesystem::path& path) {
    const std::u8string text = path.filename().u8string();
    return std::string(reinterpret_cast<const char*>(text.data()), text.size());
}

}  // namespace

TEST_CASE("a project folder is the sha256 and the name is only a label") {
    TempDir temp("bt-project-folder");
    const std::string hash(64, 'a');
    const auto legacy = temp.path / "htdemucs_6s" / hash;
    std::filesystem::create_directories(legacy);

    const auto source = temp.path / std::filesystem::path(std::u8string(u8"雨聲.wav"));
    const auto located = bt::locate_project(temp.path, "htdemucs_6s", hash, "雨聲", source);
    REQUIRE(located.has_value());
    REQUIRE(located->directory == legacy);
    REQUIRE(located->name == "雨聲");

    const auto committed = bt::commit_project(*located);
    REQUIRE(committed.has_value());
    REQUIRE(committed->name == "雨聲");
    REQUIRE(committed->hash == hash);
    REQUIRE(committed->directory == legacy);
    REQUIRE(utf8_filename(committed->directory) == hash);
    REQUIRE(std::filesystem::exists(committed->directory / "song.txt"));

    const auto listed = bt::list_projects(temp.path, "htdemucs_6s");
    REQUIRE(listed.size() == 1);
    REQUIRE(listed[0].name == "雨聲");
    REQUIRE(listed[0].hash == hash);

    const auto renamed = bt::rename_project(committed->directory, "新的名字");
    REQUIRE(renamed.has_value());
    REQUIRE(renamed->name == "新的名字");
    REQUIRE(renamed->hash == hash);
    REQUIRE(renamed->directory == committed->directory);
    REQUIRE(std::filesystem::exists(renamed->directory / "song.txt"));

    const auto other = temp.path / std::filesystem::path(std::u8string(u8"other.wav"));
    const auto again = bt::locate_project(temp.path, "htdemucs_6s", hash, "別的檔名", other);
    REQUIRE(again.has_value());
    REQUIRE(again->name == "新的名字");
    REQUIRE(again->directory == renamed->directory);

    const std::string fresh(64, 'c');
    const auto created = bt::locate_project(temp.path, "htdemucs_6s", fresh, "Bôa", source);
    REQUIRE(created.has_value());
    REQUIRE(created->name == "Bôa");
    std::u8string boa(u8"Bôa_");
    boa.append(reinterpret_cast<const char8_t*>(fresh.data()), fresh.size());
    const auto named = temp.path / "htdemucs_6s" / std::filesystem::path(boa);
    bool name_fits = true;
    try {
        (void)named.filename().string();
    } catch (const std::exception&) {
        name_fits = false;
    }
    if (name_fits) {
        REQUIRE(created->directory == named);
    } else {
        REQUIRE(created->directory == temp.path / "htdemucs_6s" / fresh);
    }

    const std::string plain(64, 'd');
    const auto labeled = bt::locate_project(temp.path, "htdemucs_6s", plain, "song", source);
    REQUIRE(labeled.has_value());
    REQUIRE(labeled->directory.filename() == std::filesystem::path("song_" + plain));

    REQUIRE(bt::write_project_seconds(renamed->directory, 12.5).has_value());
    const auto timed = bt::list_projects(temp.path, "htdemucs_6s");
    REQUIRE(timed.size() == 1);
    REQUIRE_THAT(timed[0].seconds, Catch::Matchers::WithinAbs(12.5, 1e-6));

    const auto empty = bt::rename_project(renamed->directory, "   ");
    REQUIRE_FALSE(empty.has_value());
    REQUIRE(empty.error() == "song name is empty");
    REQUIRE(bt::delete_project(renamed->directory).has_value());
    REQUIRE(bt::list_projects(temp.path, "htdemucs_6s").empty());
}

TEST_CASE("an older display-name folder is still found by its sha256") {
    TempDir temp("bt-project-named-folder");
    const std::string hash(64, 'b');
    std::u8string folder(u8"雨聲_");
    folder.append(reinterpret_cast<const char8_t*>(hash.data()), hash.size());
    const auto legacy = temp.path / "htdemucs_6s" / std::filesystem::path(folder);
    std::filesystem::create_directories(legacy);
    const auto source = temp.path / "song.wav";
    const auto located = bt::locate_project(temp.path, "htdemucs_6s", hash, "別的", source);
    REQUIRE(located.has_value());
    REQUIRE(located->directory == legacy);
    const auto committed = bt::commit_project(*located);
    REQUIRE(committed.has_value());
    REQUIRE(committed->directory == legacy);
    REQUIRE(committed->name == "別的");
    REQUIRE(bt::list_projects(temp.path, "htdemucs_6s").size() == 1);
}

TEST_CASE("song.txt keeps the separation model and device") {
    TempDir temp("bt-project-inference");
    const std::string hash(64, 'f');
    const auto dir = temp.path / "bs_roformer_sw" / hash;
    std::filesystem::create_directories(dir);
    bt::ProjectEntry entry;
    entry.name = "歌";
    entry.hash = hash;
    entry.directory = dir;
    entry.source = temp.path / "a.wav";
    entry.seconds = 3;
    entry.model_key = "bs_roformer_sw";
    entry.device = "CUDA";
    const auto committed = bt::commit_project(entry);
    REQUIRE(committed.has_value());
    REQUIRE(committed->model_key == "bs_roformer_sw");
    REQUIRE(committed->device == "CUDA");

    const auto listed = bt::list_projects(temp.path, "bs_roformer_sw");
    REQUIRE(listed.size() == 1);
    REQUIRE(listed[0].model_key == "bs_roformer_sw");
    REQUIRE(listed[0].device == "CUDA");

    const auto renamed = bt::rename_project(dir, "另一個");
    REQUIRE(renamed.has_value());
    REQUIRE(renamed->name == "另一個");
    REQUIRE(renamed->model_key == "bs_roformer_sw");
    REQUIRE(renamed->device == "CUDA");
    REQUIRE(bt::write_project_seconds(dir, 4).has_value());
    const auto again = bt::read_project(dir);
    REQUIRE(again.has_value());
    REQUIRE(again->name == "另一個");
    REQUIRE(again->device == "CUDA");
    REQUIRE_THAT(again->seconds, Catch::Matchers::WithinAbs(4.0, 1e-6));
}

TEST_CASE("a project without an inference line takes the model from its folder") {
    TempDir temp("bt-project-infer-folder");
    const std::string hash(64, '9');
    const auto dir = temp.path / "htdemucs_6s" / hash;
    std::filesystem::create_directories(dir);
    std::ofstream output(dir / "song.txt", std::ios::binary);
    output << "bt-song 1\n歌\n1.5\nC:/song.wav\n";
    output.close();
    const auto read = bt::read_project(dir);
    REQUIRE(read.has_value());
    REQUIRE(read->model_key == "htdemucs_6s");
    REQUIRE(read->device.empty());
    REQUIRE(read->name == "歌");
}

TEST_CASE("project folders move into saves and a named folder wins") {
    TempDir temp("bt-project-saves");
    const std::string hash(64, 'e');
    const auto legacy_root = temp.path / "cache" / "htdemucs_6s";
    const auto bare = legacy_root / hash;
    std::u8string named_text(u8"song_");
    named_text.append(reinterpret_cast<const char8_t*>(hash.data()), hash.size());
    const auto named = legacy_root / std::filesystem::path(named_text);
    std::filesystem::create_directories(bare);
    std::filesystem::create_directories(named);
    const auto saves = temp.path / "saves";
    bt::migrate_project_folders(legacy_root, saves);
    REQUIRE(std::filesystem::exists(saves / hash));
    REQUIRE(std::filesystem::exists(saves / std::filesystem::path(named_text)));
    REQUIRE_FALSE(std::filesystem::exists(bare));
    const auto listed = bt::list_projects(saves, "");
    REQUIRE(listed.size() == 1);
    REQUIRE(listed[0].hash == hash);
    REQUIRE(listed[0].directory == saves / std::filesystem::path(named_text));
}

TEST_CASE("playback.txt stays with its song folder") {
    TempDir temp("bt-project-playback");
    const auto song = temp.path / "song";
    const auto other = temp.path / "other";
    std::filesystem::create_directories(song);
    std::filesystem::create_directories(other);

    bt::ProjectPlayback untouched = bt::default_project_playback();
    untouched.speed = 1.2f;
    REQUIRE_FALSE(bt::read_project_playback(song, untouched));
    REQUIRE_THAT(untouched.speed, Catch::Matchers::WithinAbs(1.2f, 1e-6));

    std::ofstream foreign(song / "playback.txt", std::ios::binary);
    foreign << "bt-song 1\n";
    foreign.close();
    REQUIRE_FALSE(bt::read_project_playback(song, untouched));
    REQUIRE_THAT(untouched.speed, Catch::Matchers::WithinAbs(1.2f, 1e-6));

    bt::ProjectPlayback playback = bt::default_project_playback();
    playback.gain = {0.5f, 1.f, 1.f, 1.f, 0.25f, 2.f};
    playback.pan[4] = -0.5f;
    playback.mute[1] = true;
    playback.solo[4] = true;
    playback.speed = 0.8f;
    playback.master_db = -6.f;
    playback.loop = true;
    playback.loop_a = 1.25;
    playback.loop_b = 8.5;
    playback.loop_snap = true;
    playback.count_in = true;
    playback.metro = false;
    playback.click_db = -12.f;
    playback.beat_division = "triplet";
    playback.grid = false;
    playback.grid_division = "quarter";
    playback.verify[4].original.db = -6.f;
    playback.verify[4].original.pan = 0.25f;
    playback.verify[4].original.solo = true;
    playback.verify[1].midi.db = 3.f;
    playback.verify[1].midi.mute = false;
    playback.verify[1].midi.solo = true;
    playback.guitar_pitch_min = 45;
    playback.guitar_pitch_max = 80;
    playback.fret_prefer = 5;
    playback.fret_suggest = true;
    playback.fret_show_alt = false;
    REQUIRE(bt::write_project_playback(song, playback).has_value());
    const auto missing = bt::write_project_playback(temp.path / "missing", playback);
    REQUIRE_FALSE(missing.has_value());
    REQUIRE(missing.error() == "song folder is missing");

    bt::ProjectPlayback loaded;
    REQUIRE(bt::read_project_playback(song, loaded));
    REQUIRE_THAT(loaded.gain[0], Catch::Matchers::WithinAbs(0.5f, 1e-5));
    REQUIRE_THAT(loaded.gain[4], Catch::Matchers::WithinAbs(0.25f, 1e-5));
    REQUIRE_THAT(loaded.pan[4], Catch::Matchers::WithinAbs(-0.5f, 1e-5));
    REQUIRE(loaded.mute[1]);
    REQUIRE_FALSE(loaded.mute[0]);
    REQUIRE(loaded.solo[4]);
    REQUIRE_THAT(loaded.speed, Catch::Matchers::WithinAbs(0.8f, 1e-5));
    REQUIRE_THAT(loaded.master_db, Catch::Matchers::WithinAbs(-6.f, 1e-4));
    REQUIRE(loaded.loop);
    REQUIRE_THAT(loaded.loop_a, Catch::Matchers::WithinAbs(1.25, 1e-6));
    REQUIRE_THAT(loaded.loop_b, Catch::Matchers::WithinAbs(8.5, 1e-6));
    REQUIRE(loaded.loop_snap);
    REQUIRE(loaded.count_in);
    REQUIRE_FALSE(loaded.metro);
    REQUIRE_THAT(loaded.click_db, Catch::Matchers::WithinAbs(-12.f, 1e-4));
    REQUIRE(loaded.beat_division == "triplet");
    REQUIRE_FALSE(loaded.grid);
    REQUIRE(loaded.grid_division == "quarter");
    REQUIRE_THAT(loaded.verify[4].original.db, Catch::Matchers::WithinAbs(-6.f, 1e-4));
    REQUIRE_THAT(loaded.verify[4].original.pan, Catch::Matchers::WithinAbs(0.25f, 1e-5));
    REQUIRE(loaded.verify[4].original.solo);
    REQUIRE_FALSE(loaded.verify[4].original.mute);
    REQUIRE(loaded.verify[0].midi.mute);
    REQUIRE_FALSE(loaded.verify[1].midi.mute);
    REQUIRE(loaded.verify[1].midi.solo);
    REQUIRE_THAT(loaded.verify[1].midi.db, Catch::Matchers::WithinAbs(3.f, 1e-4));
    REQUIRE(loaded.guitar_pitch_min == 45);
    REQUIRE(loaded.guitar_pitch_max == 80);
    REQUIRE(loaded.fret_prefer == 5);
    REQUIRE(loaded.fret_suggest);
    REQUIRE_FALSE(loaded.fret_show_alt);
    REQUIRE(loaded.bass_pitch_min == 28);
    REQUIRE(loaded.key_max == 108);

    std::ofstream partial(song / "playback.txt", std::ios::binary);
    partial << "bt-playback 1\nspeed=1.5\ngain=1,2,3\nbeat_division=nope\n";
    partial.close();
    bt::ProjectPlayback sparse;
    REQUIRE(bt::read_project_playback(song, sparse));
    REQUIRE_THAT(sparse.speed, Catch::Matchers::WithinAbs(1.5f, 1e-5));
    REQUIRE_THAT(sparse.gain[0], Catch::Matchers::WithinAbs(1.f, 1e-5));
    REQUIRE(sparse.metro);
    REQUIRE(sparse.beat_division == "beat");
    REQUIRE(sparse.grid);
    REQUIRE(sparse.grid_division == "beat");

    // A file saved before the grid had its own division follows the metronome division.
    std::ofstream legacy(song / "playback.txt", std::ios::binary);
    legacy << "bt-playback 1\nbeat_division=triplet\n";
    legacy.close();
    bt::ProjectPlayback old_file;
    REQUIRE(bt::read_project_playback(song, old_file));
    REQUIRE(old_file.beat_division == "triplet");
    REQUIRE(old_file.grid_division == "triplet");
    REQUIRE(sparse.verify[0].midi.mute);
    REQUIRE_FALSE(sparse.verify[0].original.mute);
    REQUIRE_THAT(sparse.verify[0].original.db, Catch::Matchers::WithinAbs(0.f, 1e-5));
    REQUIRE_FALSE(sparse.fret_suggest);
    REQUIRE(sparse.fret_show_alt);
    REQUIRE(sparse.guitar_pitch_min == 40);

    REQUIRE(bt::write_project_playback(song, playback).has_value());
    bt::copy_project_playback(song, other);
    bt::ProjectPlayback copied;
    REQUIRE(bt::read_project_playback(other, copied));
    REQUIRE(copied.beat_division == "triplet");
    REQUIRE(copied.solo[4]);
    const auto absent = temp.path / "absent";
    std::filesystem::create_directories(absent);
    bt::copy_project_playback(temp.path / "empty-source", absent);
    REQUIRE_FALSE(std::filesystem::exists(absent / "playback.txt"));
}
