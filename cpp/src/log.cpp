#include "bt/log.hpp"

#include <mutex>
#include <utility>

namespace bt {
namespace {

std::mutex gate;
LogSink sink;

}  // namespace

void set_log_sink(LogSink next) {
    std::lock_guard lock(gate);
    sink = std::move(next);
}

void clear_log_sink() {
    std::lock_guard lock(gate);
    sink = {};
}

void log(LogLevel level, std::string_view text) {
    LogSink copy;
    {
        std::lock_guard lock(gate);
        copy = sink;
    }
    if (copy) {
        copy(level, text);
    }
}

}  // namespace bt
