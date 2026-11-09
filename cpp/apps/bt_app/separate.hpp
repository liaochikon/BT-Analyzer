#pragma once

#include "bt/separator.hpp"

#include <expected>
#include <filesystem>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <string_view>
#include <vector>

namespace btapp {

enum class InferenceDevice { Auto, Cpu, Cuda };

struct BackendStatus {
    std::string key;
    std::string label;
    bool available = false;
};

std::string model_label(std::string_view key);

// Owns the stem graph used for the next import. An open project keeps the wavs it already has.
class StemEngine {
public:
    StemEngine() = default;
    ~StemEngine();

    StemEngine(const StemEngine&) = delete;
    StemEngine& operator=(const StemEngine&) = delete;

    void discover(const std::filesystem::path& exe_dir, const std::filesystem::path& cwd);
    void apply(std::string model_key, InferenceDevice device, int threads);

    std::expected<bt::Stems, std::string> separate(
        const bt::Audio& audio, const std::function<void(float)>& progress, const std::function<bool()>& cancelled) const;

    std::string model_key() const;
    std::string device() const;
    std::string error() const;
    int chunk() const;
    std::vector<BackendStatus> backends() const;

private:
    struct Loaded;

    mutable std::mutex mu_;
    std::shared_ptr<Loaded> loaded_;
    std::string key_ = std::string(bt::kModelKey);
    std::string device_;
    std::string error_;
    int chunk_ = 0;
    std::filesystem::path fp16_;
    std::filesystem::path fp32_;
    std::filesystem::path htdemucs_;
};

}  // namespace btapp
