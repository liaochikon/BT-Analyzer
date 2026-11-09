#pragma once

#include "bt/separator.hpp"

#include <expected>
#include <filesystem>
#include <memory>
#include <string>

namespace bt {

class OnnxSegmentModel {
public:
    // intra_op_threads <= 0 keeps the runtime default; otherwise clamps to that count.
    static std::expected<OnnxSegmentModel, std::string> open(
        const std::filesystem::path& model_path, int intra_op_threads = 0);

    OnnxSegmentModel(OnnxSegmentModel&&) noexcept;
    OnnxSegmentModel& operator=(OnnxSegmentModel&&) noexcept;
    ~OnnxSegmentModel();

    OnnxSegmentModel(const OnnxSegmentModel&) = delete;
    OnnxSegmentModel& operator=(const OnnxSegmentModel&) = delete;

    // Runs the static 7.8 s graph. `segment_samples` must be kSegmentSamples.
    void run(const float* mix, float* stems, int segment_samples) const;

    SegmentRunner runner() const;

private:
    struct Impl;
    explicit OnnxSegmentModel(std::unique_ptr<Impl> impl);
    std::unique_ptr<Impl> impl_;
};

}  // namespace bt
