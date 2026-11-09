#pragma once

#include "bt/separator.hpp"

#include <array>
#include <expected>
#include <filesystem>
#include <functional>
#include <string>
#include <vector>

namespace bt {

// BS-RoFormer SW host contract. The graph sees one fixed STFT chunk.
// torch.stft center=True, n_fft=2048, hop=512, periodic Hann, normalized=False.
// Chunk length is (T - 1) * hop, read from the ONNX time axis.
// Weights stay on disk. Release zips do not carry them.
inline constexpr int kRoformerFft = 2048;
inline constexpr int kRoformerHop = 512;
inline constexpr int kRoformerBins = kRoformerFft / 2 + 1;
inline constexpr int kRoformerOverlap = 2;
inline constexpr std::string_view kRoformerFile = "models/bs_roformer_sw.onnx";

// Checkpoint order is bass, drums, other, vocals, guitar, piano.
// These are indexes into kStemNames.
inline constexpr std::array<int, kStemCount> kRoformerStemToApp{1, 0, 2, 3, 4, 5};

// mix is stereo channel-major of `chunk_samples` frames.
// stems is app stem order, channel-major, length kStemCount * 2 * chunk_samples.
using RoformerChunkFn = std::function<std::expected<void, std::string>(
    const float* mix, float* stems, int chunk_samples)>;

// ZFTurbo generic overlap-add: half-chunk step, linear edge fade, reflect padding.
// No file-level mean/std normalization. `cancelled` is checked between chunks.
std::expected<Stems, std::string> separate_roformer(
    const Audio& audio, const RoformerChunkFn& run, int chunk_samples,
    const std::function<void(float)>& progress = {},
    const std::function<bool()>& cancelled = {});

// torch.fft.rfft of one 2048-point real frame. `real` and `imag` hold 1025 bins.
void rfft_2048(const float* frame, float* real, float* imag);

// STFT then iSTFT. `frames` must be a positive multiple of kRoformerHop.
// Empty when the length is not usable.
std::vector<float> roformer_stft_roundtrip(const float* samples, int frames);

class BsRoformerModel {
public:
    // `require_cuda` keeps a CPU session from standing in for a missing GPU.
    // fp16 compute is slower on this CPU, so the app only keeps that graph when CUDA attaches.
    static std::expected<BsRoformerModel, std::string> open(
        const std::filesystem::path& model_path, int intra_op_threads = 0, bool require_cuda = false);

    // Prefer the fp16 graph on CUDA. Without CUDA, use the fp32 graph.
    static std::expected<BsRoformerModel, std::string> open_fastest(
        const std::filesystem::path& fp16_path, const std::filesystem::path& fp32_path, int intra_op_threads = 0);

    BsRoformerModel(BsRoformerModel&&) noexcept;
    BsRoformerModel& operator=(BsRoformerModel&&) noexcept;
    ~BsRoformerModel();

    BsRoformerModel(const BsRoformerModel&) = delete;
    BsRoformerModel& operator=(const BsRoformerModel&) = delete;

    int chunk_samples() const;
    const std::string& device() const;

    std::expected<void, std::string> run_chunk(const float* mix, float* stems) const;

    std::expected<Stems, std::string> separate(
        const Audio& audio, const std::function<void(float)>& progress = {},
        const std::function<bool()>& cancelled = {}) const;

private:
    struct Impl;
    explicit BsRoformerModel(std::unique_ptr<Impl> impl);
    std::unique_ptr<Impl> impl_;
};

}  // namespace bt
