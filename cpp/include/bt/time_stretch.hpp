#pragma once

#include <cstddef>
#include <functional>
#include <vector>

namespace bt {

// Pitch-preserving WSOLA. `source` writes interleaved frames and returns how many it had.
// A return of 0 means `start` is at or past the end. Called only from the thread that pulls.
class TimeStretcher {
public:
    using Source = std::function<int(int start, int frames, float* interleaved)>;

    TimeStretcher(int channels = 2, int frame = 2048, int synthesis_hop = 512, int search = 256);

    void set_source(Source source);
    void set_rate(float rate);
    void reset(double source_position);

    // Writes interleaved output. Returns a short count at the end, then 0.
    int pull(float* interleaved, int frames);
    double position() const;

private:
    bool synthesis_step();
    void flush_tail();
    bool read_frame(double position, float* interleaved);
    void read_mono(double position, int length, float* mono);
    int best_delta(const float* natural, const float* region) const;

    int channels_ = 2;
    int frame_ = 2048;
    int hop_ = 512;
    int search_ = 256;
    float rate_ = 1.f;
    double analysis_ = 0.0;
    int delta_ = 0;
    bool eof_ = false;

    Source source_;
    std::vector<float> window_;
    std::vector<float> accumulator_;
    std::vector<float> weight_;
    std::vector<float> frame_buffer_;
    std::vector<float> scratch_;
    std::vector<float> natural_;
    std::vector<float> region_;
    std::vector<float> pending_;
    std::size_t pending_read_ = 0;
};

}  // namespace bt
