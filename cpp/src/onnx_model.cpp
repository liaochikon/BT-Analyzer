#include "bt/onnx_model.hpp"

#include <onnxruntime_c_api.h>

#include <vector>

namespace bt {
namespace {

std::string status_message(const OrtApi* api, OrtStatus* status) {
    if (status == nullptr) {
        return {};
    }
    std::string message = api->GetErrorMessage(status);
    api->ReleaseStatus(status);
    return message;
}

}  // namespace

struct OnnxSegmentModel::Impl {
    const OrtApi* api = nullptr;
    OrtEnv* env = nullptr;
    OrtSessionOptions* options = nullptr;
    OrtSession* session = nullptr;
    OrtMemoryInfo* memory = nullptr;

    ~Impl() {
        if (api == nullptr) {
            return;
        }
        if (memory != nullptr) {
            api->ReleaseMemoryInfo(memory);
        }
        if (session != nullptr) {
            api->ReleaseSession(session);
        }
        if (options != nullptr) {
            api->ReleaseSessionOptions(options);
        }
        if (env != nullptr) {
            api->ReleaseEnv(env);
        }
    }
};

OnnxSegmentModel::OnnxSegmentModel(std::unique_ptr<Impl> impl) : impl_(std::move(impl)) {}

OnnxSegmentModel::OnnxSegmentModel(OnnxSegmentModel&&) noexcept = default;
OnnxSegmentModel& OnnxSegmentModel::operator=(OnnxSegmentModel&&) noexcept = default;
OnnxSegmentModel::~OnnxSegmentModel() = default;

std::expected<OnnxSegmentModel, std::string> OnnxSegmentModel::open(
    const std::filesystem::path& model_path, int intra_op_threads) {
    const OrtApi* api = OrtGetApiBase()->GetApi(ORT_API_VERSION);
    if (api == nullptr) {
        return std::unexpected("ONNX Runtime API is unavailable");
    }
    const std::wstring path = model_path.wstring();
    // Full optimization of this 7.8 s graph asks for one large planning block.
    // That request fails once Direct3D is already reserving address space.
    auto attempt = [&](GraphOptimizationLevel level, bool memory_pattern,
                       int threads) -> std::expected<OnnxSegmentModel, std::string> {
    auto impl = std::make_unique<Impl>();
    impl->api = api;
    if (auto message = status_message(api, api->CreateEnv(ORT_LOGGING_LEVEL_WARNING, "bt", &impl->env));
        !message.empty()) {
        return std::unexpected(message);
    }
    if (auto message = status_message(api, api->CreateSessionOptions(&impl->options)); !message.empty()) {
        return std::unexpected(message);
    }
    if (auto message = status_message(api, api->SetSessionGraphOptimizationLevel(impl->options, level));
        !message.empty()) {
        return std::unexpected(message);
    }
    if (!memory_pattern) {
        if (auto message = status_message(api, api->DisableMemPattern(impl->options)); !message.empty()) {
            return std::unexpected(message);
        }
        if (auto message = status_message(api, api->DisableCpuMemArena(impl->options)); !message.empty()) {
            return std::unexpected(message);
        }
    }
    if (threads > 0) {
        if (auto message = status_message(api, api->SetIntraOpNumThreads(impl->options, threads));
            !message.empty()) {
            return std::unexpected(message);
        }
    }
    if (auto message = status_message(
            api, api->CreateSession(impl->env, path.c_str(), impl->options, &impl->session));
        !message.empty()) {
        return std::unexpected(message);
    }
    if (auto message = status_message(
            api, api->CreateCpuMemoryInfo(OrtDeviceAllocator, OrtMemTypeDefault, &impl->memory));
        !message.empty()) {
        return std::unexpected(message);
    }

    OrtAllocator* allocator = nullptr;
    if (auto message = status_message(api, api->GetAllocatorWithDefaultOptions(&allocator)); !message.empty()) {
        return std::unexpected(message);
    }
    char* input_name = nullptr;
    char* output_name = nullptr;
    if (auto message = status_message(api, api->SessionGetInputName(impl->session, 0, allocator, &input_name));
        !message.empty()) {
        return std::unexpected(message);
    }
    if (auto message = status_message(api, api->SessionGetOutputName(impl->session, 0, allocator, &output_name));
        !message.empty()) {
        allocator->Free(allocator, input_name);
        return std::unexpected(message);
    }
    const bool names_ok = input_name == kInputName && output_name == kOutputName;
    allocator->Free(allocator, input_name);
    allocator->Free(allocator, output_name);
    if (!names_ok) {
        return std::unexpected("ONNX model inputs must be named mix and stems");
    }
    return OnnxSegmentModel(std::move(impl));
    };
    const int preferred = intra_op_threads > 0 ? intra_op_threads : 0;
    auto opened = attempt(ORT_ENABLE_ALL, true, preferred);
    if (opened || opened.error().find("bad allocation") == std::string::npos) {
        return opened;
    }
    const int fallback = preferred > 0 ? preferred : 4;
    return attempt(ORT_ENABLE_BASIC, false, fallback);
}

void OnnxSegmentModel::run(const float* mix, float* stems, int segment_samples) const {
    if (segment_samples != kSegmentSamples) {
        throw std::runtime_error("htdemucs_6s ONNX graph expects a 343980-sample segment");
    }
    const OrtApi* api = impl_->api;
    const int64_t shape[] = {1, 2, kSegmentSamples};
    const std::size_t bytes = sizeof(float) * 2 * static_cast<std::size_t>(kSegmentSamples);
    OrtValue* input = nullptr;
    auto fail = [&](OrtStatus* status) {
        if (input != nullptr) {
            api->ReleaseValue(input);
        }
        throw std::runtime_error(status_message(api, status));
    };
    if (OrtStatus* status = api->CreateTensorWithDataAsOrtValue(
            impl_->memory, const_cast<float*>(mix), bytes, shape, 3, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, &input)) {
        fail(status);
    }

    const char* input_names[] = {"mix"};
    const char* output_names[] = {"stems"};
    OrtValue* output = nullptr;
    if (OrtStatus* status = api->Run(
            impl_->session, nullptr, input_names, &input, 1, output_names, 1, &output)) {
        fail(status);
    }
    float* data = nullptr;
    if (OrtStatus* status = api->GetTensorMutableData(output, reinterpret_cast<void**>(&data))) {
        api->ReleaseValue(output);
        fail(status);
    }
    std::copy_n(data, kStemCount * 2 * kSegmentSamples, stems);
    api->ReleaseValue(output);
    api->ReleaseValue(input);
}

SegmentRunner OnnxSegmentModel::runner() const {
    return [this](const float* mix, float* stems, int segment_samples) {
        run(mix, stems, segment_samples);
    };
}

}  // namespace bt
