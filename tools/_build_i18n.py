"""Build locales/*.txt and rewrite bt_app sources to use t(\"key\")."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "cpp/apps/bt_app"

# Extra UI keys not covered by Phrase constants.
EXTRA: dict[str, tuple[str, str, str]] = {
    # song phase
    "phase.decoding": ("解碼中", "解码中", "Decoding"),
    "phase.separating": ("分離中", "分离中", "Separating"),
    "phase.cancelled": ("已取消", "已取消", "Cancelled"),
    "phase.failed": ("失敗", "失败", "Failed"),
    "phase.empty": ("", "", ""),
    "phase.ready": ("就緒", "就绪", "Ready"),
    "phase.ready_cache": ("就緒 · 快取", "就绪 · 缓存", "Ready · cache"),
    "phase.no_song": ("沒有歌曲", "没有歌曲", "No song"),
    # monitor / log
    "note.idle": ("閒置", "闲置", "Idle"),
    "note.running": ("轉譜中", "转谱中", "Running"),
    "note.ready": ("完成", "完成", "Ready"),
    "note.failed": ("失敗", "失败", "Failed"),
    "log.open_song": ("開啟歌曲", "打开歌曲", "Open song"),
    "log.separating": ("開始分離", "开始分离", "Separating"),
    "log.song_ready": ("歌曲就緒", "歌曲就绪", "Song ready"),
    "log.song_ready_cache": ("歌曲就緒 · 快取", "歌曲就绪 · 缓存", "Song ready · cache"),
    "log.separation_cancelled": ("分離已取消", "分离已取消", "Separation cancelled"),
    "log.song_failed": ("歌曲失敗", "歌曲失败", "Song failed"),
    "log.song_closed": ("已關閉歌曲", "已关闭歌曲", "Song closed"),
    "log.started": ("啟動", "启动", "Started"),
    "log.playback_ready": ("播放裝置就緒", "播放设备就绪", "Playback device ready"),
    "log.separation_model_ready": ("分離模型就緒", "分离模型就绪", "Separation model ready"),
    "log.transcription_model_ready": ("轉譜模型就緒", "转谱模型就绪", "Transcription model ready"),
    "log.missing_nmp": ("找不到 models/nmp.onnx", "找不到 models/nmp.onnx", "models/nmp.onnx was not found"),
    "log.transcribe_start": ("轉譜開始", "转谱开始", "Transcribe"),
    "log.transcribe_done": ("轉譜完成", "转谱完成", "Transcribed"),
    "log.transcribe_failed": ("轉譜失敗", "转谱失败", "Transcribe failed"),
    "log.rerun_tail": (" · 重跑", " · 重跑", " · rerun"),
    "log.beat_start": ("拍點偵測開始", "拍点检测开始", "Beat tracking started"),
    "log.beat_done": ("拍點完成", "拍点完成", "Beats ready"),
    "log.beat_failed": ("拍點失敗", "拍点失败", "Beat tracking failed"),
    "log.beat_cancelled": ("拍點已取消", "拍点已取消", "Beat tracking cancelled"),
    "monitor.machine": ("本機", "本机", "Machine"),
    "monitor.app": ("程序", "进程", "App"),
    "monitor.system": ("系統", "系统", "System"),
    "monitor.memory": ("記憶體", "内存", "Memory"),
    "monitor.cores": ("核心", "核心", "Cores"),
    "monitor.song": ("歌曲", "歌曲", "Song"),
    "monitor.playing": ("播放", "播放", "Playing"),
    "monitor.stopped": ("停止", "停止", "Stopped"),
    "monitor.jobs": ("工作", "工作", "Jobs"),
    "monitor.transcribing": ("轉譜中", "转谱中", "Transcribing"),
    "monitor.masking": ("遮罩中", "遮罩中", "Masking"),
    "monitor.detecting": ("偵測中", "检测中", "Detecting"),
    "monitor.beats": ("拍點", "拍点", "Beats"),
    "monitor.log": ("紀錄", "记录", "Log"),
    "monitor.clear_view": ("清除畫面", "清除画面", "Clear view"),
    "monitor.copy_all": ("複製全部", "复制全部", "Copy all"),
    "monitor.title": ("系統監控", "系统监控", "Monitor"),
    # settings extras
    "settings.performance": ("效能", "性能", "Performance"),
    "settings.note_workers": ("轉譜並行", "转谱并行", "Note workers"),
    "settings.separation_threads": ("分軌執行緒", "分轨线程", "Separation threads"),
    "settings.lang_hant": ("繁體中文", "繁體中文", "Traditional Chinese"),
    "settings.lang_hans": ("简体中文", "简体中文", "Simplified Chinese"),
    "settings.lang_en": ("English", "English", "English"),
    # shortcuts UI
    "shortcut.heading": ("快捷鍵", "快捷键", "Shortcuts"),
    "shortcut.reset": ("還原", "还原", "Reset"),
    "shortcut.press": ("請按鍵…", "请按键...", "Press key..."),
    "shortcut.reserved": ("留給系統", "留给系统", "Reserved"),
    "shortcut.no_modifiers": ("請用不含修飾鍵的按鍵", "请用不含修饰键的按键", "Use a key without modifiers"),
    "shortcut.reset_all": ("還原全部快捷鍵", "还原全部快捷键", "Reset all shortcuts"),
    "action.play_pause": ("播放／暫停", "播放／暂停", "Play / Pause"),
    "action.stop": ("停止", "停止", "Stop"),
    "action.seek_back": ("後退", "后退", "Seek back"),
    "action.seek_forward": ("前進", "前进", "Seek forward"),
    "action.loop": ("循環", "循环", "Loop"),
    "action.clear_loop": ("清除循環", "清除循环", "Clear loop"),
    "action.metro": ("節拍器", "节拍器", "Metronome"),
    "action.count_in": ("數四拍", "数四拍", "Count in"),
    "action.snap": ("對拍", "对拍", "Snap"),
    "action.mute": ("靜音選取軌", "静音选取轨", "Mute selected"),
    "action.solo": ("獨奏選取軌", "独奏选取轨", "Solo selected"),
    "action.prev_track": ("上一軌", "上一轨", "Previous track"),
    "action.next_track": ("下一軌", "下一轨", "Next track"),
    "action.view_guitar": ("吉他視圖", "吉他视图", "Guitar view"),
    "action.view_bass": ("貝斯視圖", "贝斯视图", "Bass view"),
    "action.view_fall": ("鋼琴", "钢琴", "Piano"),
    "model_tag_fast": ("(快速)", "(快速)", "(Fast)"),
    "model_tag_quality": ("(品質)", "(品质)", "(Quality)"),
    "action.adjust_speed": ("速度 −／+", "速度 −／+", "Speed − / +"),
    "action.adjust_seek": ("步進 −／+", "步进 −／+", "Seek step − / +"),
    "action.adjust_master": ("主音量 −／+", "主音量 −／+", "Master − / +"),
    "action.adjust_click": ("節拍器音量 −／+", "节拍器音量 −／+", "Click − / +"),
    "action.adjust_track_gain": ("選取軌音量 −／+", "选取轨音量 −／+", "Track volume − / +"),
    "action.adjust_track_pan": ("選取軌聲像 左／右", "选取轨声像 左／右", "Track pan L / R"),
    # dialogs / fatal
    "dialog.audio_filter": ("音訊", "音频", "Audio"),
    "dialog.all_files": ("所有檔案", "所有文件", "All files"),
    "dialog.open_song": ("開啟歌曲", "打开歌曲", "Open song"),
    "error.d3d": ("Direct3D 11 無法啟動", "Direct3D 11 无法启动", "Direct3D 11 failed to start"),
    "error.session": ("Session 無法啟動", "Session 无法启动", "Session failed to start"),
    "error.missing_nmp": ("找不到 models/nmp.onnx", "找不到 models/nmp.onnx", "models/nmp.onnx was not found"),
    "error.missing_roformer": (
        "找不到 models/bs_roformer_sw.onnx。這份版本不附分軌權重。",
        "找不到 models/bs_roformer_sw.onnx。这份版本不附分轨权重。",
        "models/bs_roformer_sw.onnx was not found. This build does not ship stem weights.",
    ),
    "error.missing_htdemucs": (
        "找不到 models/htdemucs_6s.onnx。",
        "找不到 models/htdemucs_6s.onnx。",
        "models/htdemucs_6s.onnx was not found.",
    ),
    "error.missing_cuda_ep": (
        "找不到 onnxruntime_providers_cuda.dll",
        "找不到 onnxruntime_providers_cuda.dll",
        "onnxruntime_providers_cuda.dll was not found",
    ),
}


def snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def parse_locale(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key] = value.replace("\\n", "\n").replace("\\\\", "\\")
    return out


def load_phrases() -> dict[str, tuple[str, str, str]]:
    # Locales are the source of truth; EXTRA adds/overrides keys for regen.
    root = ROOT / "locales"
    hant = parse_locale(root / "zh-Hant.txt")
    hans = parse_locale(root / "zh-Hans.txt")
    en = parse_locale(root / "en.txt")
    keys = sorted(set(hant) | set(hans) | set(en) | set(EXTRA))
    phrases: dict[str, tuple[str, str, str]] = {
        key: (hant.get(key, ""), hans.get(key, ""), en.get(key, "")) for key in keys
    }
    phrases.update(EXTRA)
    return phrases


def write_locales(phrases: dict[str, tuple[str, str, str]]) -> None:
    out = ROOT / "locales"
    out.mkdir(exist_ok=True)
    mapping = {"zh-Hant.txt": 0, "zh-Hans.txt": 1, "en.txt": 2}
    for filename, index in mapping.items():
        lines = [f"# BT-Analyzer UI locale ({filename})", ""]
        for key in sorted(phrases):
            value = phrases[key][index]
            value = value.replace("\\", "\\\\").replace("\n", "\\n")
            lines.append(f"{key}={value}")
        (out / filename).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("wrote", filename, len(phrases))


PHRASE_TO_KEY = {
    "kOpen": "open",
    "kSettings": "settings",
    "kMonitor": "monitor",
    "kLanguage": "language",
    "kSelected": "selected",
    "kPlay": "play",
    "kCountIn": "count_in",
    "kPause": "pause",
    "kStop": "stop",
    "kLoop": "loop",
    "kLoopOff": "loop_off",
    "kClear": "clear",
    "kSnap": "snap",
    "kSpeed": "speed",
    "kSeekStep": "seek_step",
    "kMaster": "master",
    "kMute": "mute",
    "kSolo": "solo",
    "kCancel": "cancel",
    "kMetronome": "metronome",
    "kBeatAgain": "beat_again",
    "kClick": "click",
    "kGuitarView": "guitar_view",
    "kBassView": "bass_view",
    "kFall": "fall",
    "kFrets": "frets",
    "kBright": "bright",
    "kPitchMin": "pitch_min",
    "kPitchMax": "pitch_max",
    "kPitchMask": "pitch_mask",
    "kPitchMaskBuild": "pitch_mask_build",
    "kVerifyOriginal": "verify_original",
    "kVerifyMidi": "verify_midi",
    "kModeSuggest": "mode_suggest",
    "kPrefer": "prefer",
    "kAllowOpen": "allow_open",
    "kShowAlt": "show_alt",
    "kHarmonic": "harmonic",
    "kProjects": "projects",
    "kImport": "import",
    "kDelete": "delete",
    "kRename": "rename",
    "kSeparation": "separation",
    "kSeparationModel": "separation_model",
    "kInferenceDevice": "inference_device",
    "kDeviceAuto": "device_auto",
    "kDeviceCpu": "device_cpu",
    "kDeviceCuda": "device_cuda",
    "kAudio": "audio",
    "kOutput": "output",
    "kDeviceDefault": "device_default",
    "kRerun": "rerun",
    "kOnset": "onset",
    "kFrame": "frame",
    "kMinMs": "min_ms",
    "kMinHz": "min_hz",
    "kMaxHz": "max_hz",
    "kMelodia": "melodia",
    "kUnlimited": "unlimited",
    "kLook": "look",
    "kRange": "range",
    "kHand": "hand",
    "kPitchFold": "pitch_fold",
    "kTranscribe": "transcribe",
    "kPitchMissing": "pitch_missing",
}


def strip_phrase_block(text: str) -> str:
    text = re.sub(
        r"struct Phrase \{.*?\};\n\nconst char\* tr\(const Phrase& phrase, int language\) \{.*?\}\n\n",
        "",
        text,
        count=1,
        flags=re.S,
    )
    text = re.sub(
        r"const Phrase kOpen.*?\nconst Phrase kStems\[bt::kStemCount\] = \{.*?\};\n\n",
        "",
        text,
        count=1,
        flags=re.S,
    )
    if '#include "i18n.hpp"' not in text:
        text = text.replace('#include "ui.hpp"', '#include "ui.hpp"\n\n#include "i18n.hpp"', 1)
    return text


def replace_tr_calls(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        lang = match.group(2)
        key = PHRASE_TO_KEY.get(name)
        if key is None:
            raise SystemExit(f"unknown phrase {name}")
        return f't("{key}", {lang})'

    text = re.sub(r"tr\((k\w+),\s*([^)]+)\)", repl, text)
    text = text.replace(
        "tr(kStems[stem], app.language)",
        't(("stem.drums\\0stem.bass\\0stem.other\\0stem.vocals\\0stem.guitar\\0stem.piano" + 0), app.language)',
    )
    # Fix stems properly with helper call inserted later
    text = re.sub(
        r't\(\("stem\.drums\\0stem\.bass\\0stem\.other\\0stem\.vocals\\0stem\.guitar\\0stem\.piano" \+ 0\), app\.language\)',
        "stem_label(stem, app.language)",
        text,
    )
    text = text.replace("tr(kStems[stem], app.language)", "stem_label(stem, app.language)")
    text = text.replace(
        "ImGui::TextColored(ImGui::ColorConvertU32ToFloat4(kHi), \"%s\", tr(kStems[stem], app.language));",
        'ImGui::TextColored(ImGui::ColorConvertU32ToFloat4(kHi), "%s", stem_label(stem, app.language));',
    )
    # remaining kStems
    text = re.sub(
        r"tr\(kStems\[([^\]]+)\],\s*([^)]+)\)",
        r"stem_label(\1, \2)",
        text,
    )
    return text


STEM_HELPER = """
const char* stem_label(int stem, int language) {
    static const char* keys[bt::kStemCount] = {
        "stem.drums", "stem.bass", "stem.other", "stem.vocals", "stem.guitar", "stem.piano"};
    if (stem < 0 || stem >= bt::kStemCount) {
        return "";
    }
    return t(keys[stem], language);
}

"""


def main() -> None:
    phrases = load_phrases()
    write_locales(phrases)
    print("locales ready", len(phrases))


if __name__ == "__main__":
    main()
