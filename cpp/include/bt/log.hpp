#pragma once

#include <functional>
#include <string_view>

namespace bt {

// Headless-safe log hub. GUI and bt-agent install a sink; default is no-op.
enum class LogLevel { Info, Warn, Error };

using LogSink = std::function<void(LogLevel level, std::string_view text)>;

void set_log_sink(LogSink sink);
void clear_log_sink();
void log(LogLevel level, std::string_view text);

}  // namespace bt
