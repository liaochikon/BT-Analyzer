#include "bt/project.hpp"

#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include <filesystem>
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
