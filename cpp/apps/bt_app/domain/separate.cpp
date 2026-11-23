#include "separate.hpp"

#include "i18n.hpp"

#include "bt/contract.hpp"
#include "bt/onnx_model.hpp"
#include "bt/roformer.hpp"

#include <memory>
#include <mutex>
#include <utility>

namespace btapp {
namespace {

std::filesystem::path find_up(std::filesystem::path start, const char* relative) {
    std::filesystem::path cursor = std::move(start);
    for (int step = 0; step < 8 && !cursor.empty(); ++step) {
        const auto candidate = cursor / relative;
        std::error_code error;
        if (std::filesystem::exists(candidate, error)) {
            return candidate;
        }
        const auto parent = cursor.parent_path();
        if (parent == cursor) {
            break;
        }
        cursor = parent;
    }
    return {};
}

std::filesystem::path find_asset(
    const std::filesystem::path& exe_dir, const std::filesystem::path& cwd, const char* relative) {
    if (auto found = find_up(exe_dir, relative); !found.empty()) {
        return found;
    }
    return find_up(cwd, relative);
}

}  // namespace

struct StemEngine::Loaded {
    std::string key;
    std::string device;
    int chunk = 0;
    std::shared_ptr<bt::BsRoformerModel> roformer;
    std::shared_ptr<bt::OnnxSegmentModel> demucs;
};

StemEngine::~StemEngine() = default;

std::string model_label(std::string_view key) {
    if (key == "htdemucs_6s") {
        return std::string("HTDemucs 6s ") + t("model_tag_fast");
    }
    if (key == "bs_roformer_sw") {
        return std::string("BS-RoFormer SW ") + t("model_tag_quality");
    }
    return std::string(key);
}

void StemEngine::discover(const std::filesystem::path& exe_dir, const std::filesystem::path& cwd) {
    fp16_ = find_asset(exe_dir, cwd, "models/bs_roformer_sw_fp16.onnx");
    const char* fp32_names[] = {
        "models/bs_roformer_sw.onnx",
        "models/bs_roformer_sw_6stem_fp32.onnx",
        "models/bs_roformer_sw_6stem_fp16.onnx",
    };
    fp32_.clear();
    for (const char* name : fp32_names) {
        fp32_ = find_asset(exe_dir, cwd, name);
        if (!fp32_.empty()) {
            break;
        }
    }
    htdemucs_ = find_asset(exe_dir, cwd, "models/htdemucs_6s.onnx");
}

void StemEngine::apply(std::string model_key, InferenceDevice device, int threads) {
    if (!bt::is_separation_key(model_key)) {
        model_key = std::string(bt::kModelKey);
    }
    const int preference = static_cast<int>(device);
    auto fail = [&](std::string message) {
        std::lock_guard lock(mu_);
        key_ = model_key;
        device_.clear();
        error_ = std::move(message);
        chunk_ = 0;
        loaded_.reset();
    };
    auto accept = [&](std::shared_ptr<Loaded> loaded) {
        std::lock_guard lock(mu_);
        key_ = loaded->key;
        device_ = loaded->device;
        error_.clear();
        chunk_ = loaded->chunk;
        loaded_ = std::move(loaded);
    };
    auto take_roformer = [&](std::expected<bt::BsRoformerModel, std::string> opened) -> bool {
        if (!opened) {
            fail(opened.error());
            return false;
        }
        auto loaded = std::make_shared<Loaded>();
        loaded->key = model_key;
        loaded->device = opened->device();
        loaded->chunk = opened->chunk_samples();
        loaded->roformer = std::make_shared<bt::BsRoformerModel>(std::move(*opened));
        accept(std::move(loaded));
        return true;
    };

    if (model_key == "htdemucs_6s") {
        if (htdemucs_.empty()) {
            fail("error.missing_htdemucs");
            return;
        }
        auto opened = bt::OnnxSegmentModel::open(htdemucs_, threads);
        if (!opened) {
            fail(opened.error());
            return;
        }
        auto loaded = std::make_shared<Loaded>();
        loaded->key = model_key;
        loaded->device = "CPU";
        loaded->chunk = bt::kSegmentSamples;
        loaded->demucs = std::make_shared<bt::OnnxSegmentModel>(std::move(*opened));
        accept(std::move(loaded));
        return;
    }

    if (fp16_.empty() && fp32_.empty()) {
        fail("error.missing_roformer");
        return;
    }
    if (preference == static_cast<int>(InferenceDevice::Cuda)) {
        if (!fp16_.empty()) {
            auto gpu = bt::BsRoformerModel::open(fp16_, threads, true);
            if (gpu) {
                take_roformer(std::move(gpu));
                return;
            }
            if (fp32_.empty()) {
                fail(gpu.error());
                return;
            }
        }
        take_roformer(bt::BsRoformerModel::open(fp32_, threads, true));
        return;
    }
    if (preference == static_cast<int>(InferenceDevice::Cpu)) {
        if (!fp32_.empty()) {
            take_roformer(bt::BsRoformerModel::open(fp32_, threads, false));
            return;
        }
        take_roformer(bt::BsRoformerModel::open(fp16_, threads, false));
        return;
    }
    take_roformer(bt::BsRoformerModel::open_fastest(fp16_, fp32_, threads));
}

std::expected<bt::Stems, std::string> StemEngine::separate(
    const bt::Audio& audio, const std::function<void(float)>& progress, const std::function<bool()>& cancelled) const {
    std::shared_ptr<Loaded> loaded;
    std::string error;
    {
        std::lock_guard lock(mu_);
        loaded = loaded_;
        error = error_;
    }
    if (loaded == nullptr) {
        return std::unexpected(error.empty() ? std::string("error.missing_roformer") : error);
    }
    if (loaded->roformer != nullptr) {
        return loaded->roformer->separate(audio, progress, cancelled);
    }
    if (loaded->demucs == nullptr) {
        return std::unexpected(error.empty() ? std::string("error.missing_htdemucs") : error);
    }
    try {
        return bt::separate_audio(audio, loaded->demucs->runner(), {}, progress, cancelled);
    } catch (const std::exception& ex) {
        return std::unexpected(ex.what());
    }
}

std::string StemEngine::model_key() const {
    std::lock_guard lock(mu_);
    return key_;
}

std::string StemEngine::device() const {
    std::lock_guard lock(mu_);
    return device_;
}

std::string StemEngine::error() const {
    std::lock_guard lock(mu_);
    return error_;
}

int StemEngine::chunk() const {
    std::lock_guard lock(mu_);
    return chunk_;
}

std::vector<BackendStatus> StemEngine::backends() const {
    std::vector<BackendStatus> found;
    found.push_back(BackendStatus{std::string(bt::kSeparationKeys[0]), model_label(bt::kSeparationKeys[0]), !fp16_.empty() || !fp32_.empty()});
    found.push_back(BackendStatus{std::string(bt::kSeparationKeys[1]), model_label(bt::kSeparationKeys[1]), !htdemucs_.empty()});
    return found;
}

}  // namespace btapp
