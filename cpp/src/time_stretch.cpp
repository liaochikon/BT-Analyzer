#include "bt/time_stretch.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>

namespace bt {
namespace {

float hann(int index, int length) {
    return 0.5f - 0.5f * std::cos(6.283185307179586f * static_cast<float>(index) / static_cast<float>(length));
}

}  // namespace

TimeStretcher::TimeStretcher(int channels, int frame, int synthesis_hop, int search)
    : channels_(std::max(channels, 1)),
      frame_(std::max(frame, 2)),
      hop_(std::clamp(synthesis_hop, 1, frame_)),
      search_(std::max(search, 0)) {
    window_.resize(static_cast<std::size_t>(frame_));
    for (int i = 0; i < frame_; ++i) {
        window_[static_cast<std::size_t>(i)] = hann(i, frame_);
    }
    accumulator_.assign(static_cast<std::size_t>(frame_ * channels_), 0.f);
    weight_.assign(static_cast<std::size_t>(frame_), 0.f);
    frame_buffer_.resize(static_cast<std::size_t>(frame_ * channels_));
    scratch_.resize(static_cast<std::size_t>((frame_ + 2 * search_) * channels_));
    natural_.resize(static_cast<std::size_t>(frame_));
    region_.resize(static_cast<std::size_t>(frame_ + 2 * search_));
}

void TimeStretcher::set_source(Source source) {
    source_ = std::move(source);
}

void TimeStretcher::set_rate(float rate) {
    rate_ = std::max(rate, 0.1f);
}

void TimeStretcher::reset(double source_position) {
    analysis_ = std::max(0.0, source_position);
    delta_ = 0;
    eof_ = false;
    std::fill(accumulator_.begin(), accumulator_.end(), 0.f);
    std::fill(weight_.begin(), weight_.end(), 0.f);
    pending_.clear();
    pending_read_ = 0;
}

double TimeStretcher::position() const {
    return analysis_;
}

bool TimeStretcher::read_frame(double position, float* interleaved) {
    const int start = static_cast<int>(std::floor(position));
    std::fill_n(interleaved, frame_ * channels_, 0.f);
    if (!source_) {
        return start < 0;
    }
    if (start < 0) {
        const int available = frame_ + start;
        if (available <= 0) {
            return true;
        }
        const int count = std::clamp(source_(0, available, scratch_.data()), 0, available);
        for (int i = 0; i < count; ++i) {
            std::copy_n(
                scratch_.data() + static_cast<std::size_t>(i * channels_), channels_,
                interleaved + static_cast<std::size_t>((-start + i) * channels_));
        }
        return true;
    }
    const int count = source_(start, frame_, interleaved);
    if (count <= 0) {
        return false;
    }
    if (count < frame_) {
        std::fill(interleaved + count * channels_, interleaved + frame_ * channels_, 0.f);
    }
    return true;
}

void TimeStretcher::read_mono(double position, int length, float* mono) {
    std::fill_n(mono, length, 0.f);
    if (!source_ || length <= 0) {
        return;
    }
    int start = static_cast<int>(std::floor(position));
    int destination = 0;
    int request = length;
    if (start < 0) {
        destination = -start;
        if (destination >= length) {
            return;
        }
        start = 0;
        request = length - destination;
    }
    if (static_cast<int>(scratch_.size()) < request * channels_) {
        scratch_.resize(static_cast<std::size_t>(request * channels_));
    }
    const int count = std::clamp(source_(start, request, scratch_.data()), 0, request);
    for (int i = 0; i < count; ++i) {
        float sum = 0.f;
        for (int channel = 0; channel < channels_; ++channel) {
            sum += scratch_[static_cast<std::size_t>(i * channels_ + channel)];
        }
        mono[destination + i] = sum / static_cast<float>(channels_);
    }
}

int TimeStretcher::best_delta(const float* natural, const float* region) const {
    int best = 0;
    float best_score = -1.0e30f;
    const int offsets = 2 * search_ + 1;
    for (int offset = 0; offset < offsets; ++offset) {
        float dot = 0.f;
        float energy = 0.f;
        const float* window = region + offset;
        for (int i = 0; i < frame_; ++i) {
            const float sample = window[i];
            dot += sample * natural[i];
            energy += sample * sample;
        }
        const float score = dot / (std::sqrt(energy) + 1.0e-6f);
        if (score > best_score) {
            best_score = score;
            best = offset;
        }
    }
    return best - search_;
}

void TimeStretcher::flush_tail() {
    int count = 0;
    while (count < frame_ && weight_[static_cast<std::size_t>(count)] > 1.0e-4f) {
        ++count;
    }
    if (count == 0) {
        return;
    }
    const std::size_t begin = pending_.size();
    pending_.resize(begin + static_cast<std::size_t>(count * channels_));
    for (int i = 0; i < count; ++i) {
        const float scale = 1.f / std::max(weight_[static_cast<std::size_t>(i)], 1.0e-6f);
        for (int channel = 0; channel < channels_; ++channel) {
            const std::size_t index = static_cast<std::size_t>(i * channels_ + channel);
            pending_[begin + index] = accumulator_[index] * scale;
        }
    }
    std::fill(weight_.begin(), weight_.end(), 0.f);
    std::fill(accumulator_.begin(), accumulator_.end(), 0.f);
}

bool TimeStretcher::synthesis_step() {
    if (!read_frame(analysis_ + static_cast<double>(delta_), frame_buffer_.data())) {
        flush_tail();
        eof_ = true;
        return false;
    }

    for (int i = 0; i < frame_; ++i) {
        const float window = window_[static_cast<std::size_t>(i)];
        weight_[static_cast<std::size_t>(i)] += window;
        for (int channel = 0; channel < channels_; ++channel) {
            const std::size_t index = static_cast<std::size_t>(i * channels_ + channel);
            accumulator_[index] += frame_buffer_[index] * window;
        }
    }

    const std::size_t begin = pending_.size();
    pending_.resize(begin + static_cast<std::size_t>(hop_ * channels_));
    for (int i = 0; i < hop_; ++i) {
        const float scale = 1.f / std::max(weight_[static_cast<std::size_t>(i)], 1.0e-6f);
        for (int channel = 0; channel < channels_; ++channel) {
            const std::size_t index = static_cast<std::size_t>(i * channels_ + channel);
            pending_[begin + index] = accumulator_[index] * scale;
        }
    }

    std::move(
        accumulator_.begin() + static_cast<std::ptrdiff_t>(hop_ * channels_), accumulator_.end(),
        accumulator_.begin());
    std::fill(accumulator_.end() - hop_ * channels_, accumulator_.end(), 0.f);
    std::move(weight_.begin() + hop_, weight_.end(), weight_.begin());
    std::fill(weight_.end() - hop_, weight_.end(), 0.f);

    read_mono(analysis_ + static_cast<double>(delta_ + hop_), frame_, natural_.data());
    const double next = analysis_ + static_cast<double>(hop_) * static_cast<double>(rate_);
    read_mono(next - static_cast<double>(search_), frame_ + 2 * search_, region_.data());
    delta_ = best_delta(natural_.data(), region_.data());
    analysis_ = next;
    return true;
}

int TimeStretcher::pull(float* interleaved, int frames) {
    if (frames <= 0 || interleaved == nullptr) {
        return 0;
    }
    const int needed = frames * channels_;
    while (static_cast<int>(pending_.size() - pending_read_) < needed && !eof_) {
        if (!synthesis_step()) {
            break;
        }
    }
    const int available = static_cast<int>((pending_.size() - pending_read_) / static_cast<std::size_t>(channels_));
    const int count = std::min(frames, available);
    if (count > 0) {
        std::copy_n(pending_.data() + pending_read_, count * channels_, interleaved);
        pending_read_ += static_cast<std::size_t>(count * channels_);
        if (pending_read_ > 8192 && pending_read_ * 2 > pending_.size()) {
            pending_.erase(pending_.begin(), pending_.begin() + static_cast<std::ptrdiff_t>(pending_read_));
            pending_read_ = 0;
        }
    }
    return count;
}

}  // namespace bt
