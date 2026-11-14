#include "icons.hpp"

#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <d3d11.h>
#include <wincodec.h>
#include <wrl/client.h>

#pragma comment(lib, "windowscodecs.lib")

namespace btapp {
namespace {

struct GlyphUv {
    ImVec2 uv0{0, 0};
    ImVec2 uv1{0, 0};
    bool ok = false;
};

Microsoft::WRL::ComPtr<ID3D11ShaderResourceView> atlas_srv;
std::array<GlyphUv, static_cast<std::size_t>(kIconCount)> glyph_uv{};
bool atlas_loaded = false;

const char* icon_key(Icon icon) {
    switch (icon) {
    case Icon::Play:
        return "play";
    case Icon::Pause:
        return "pause";
    case Icon::Stop:
        return "stop";
    case Icon::Loop:
        return "loop";
    case Icon::Clear:
        return "clear";
    case Icon::Speaker:
        return "speaker";
    case Icon::SpeakerOff:
        return "speaker_off";
    case Icon::Solo:
        return "solo";
    case Icon::Reset:
        return "reset";
    case Icon::Gear:
        return "gear";
    case Icon::Metro:
        return "metro";
    case Icon::Pencil:
        return "pencil";
    case Icon::Trash:
        return "trash";
    case Icon::Import:
        return "import";
    case Icon::Cancel:
        return "cancel";
    case Icon::Monitor:
        return "monitor";
    case Icon::Guitar:
        return "guitar";
    case Icon::Bass:
        return "bass";
    case Icon::Positions:
        return "positions";
    case Icon::Suggest:
        return "suggest";
    case Icon::Grid:
        return "grid";
    case Icon::Detect:
        return "detect";
    case Icon::Again:
        return "again";
    case Icon::Rerun:
        return "rerun";
    case Icon::Magnet:
        return "magnet";
    case Icon::Count:
        return "count";
    }
    return "";
}

std::optional<Icon> icon_from_key(std::string_view key) {
    for (int i = 0; i < kIconCount; ++i) {
        const auto icon = static_cast<Icon>(i);
        if (key == icon_key(icon)) {
            return icon;
        }
    }
    return std::nullopt;
}

bool decode_png_rgba(const std::filesystem::path& path, std::vector<std::uint8_t>& rgba, UINT& width, UINT& height) {
    Microsoft::WRL::ComPtr<IWICImagingFactory> factory;
    if (FAILED(CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&factory)))) {
        return false;
    }
    Microsoft::WRL::ComPtr<IWICBitmapDecoder> decoder;
    if (FAILED(factory->CreateDecoderFromFilename(path.wstring().c_str(), nullptr, GENERIC_READ, WICDecodeMetadataCacheOnLoad, &decoder))) {
        return false;
    }
    Microsoft::WRL::ComPtr<IWICBitmapFrameDecode> frame;
    if (FAILED(decoder->GetFrame(0, &frame))) {
        return false;
    }
    Microsoft::WRL::ComPtr<IWICFormatConverter> converter;
    if (FAILED(factory->CreateFormatConverter(&converter))) {
        return false;
    }
    if (FAILED(converter->Initialize(
            frame.Get(), GUID_WICPixelFormat32bppRGBA, WICBitmapDitherTypeNone, nullptr, 0.0, WICBitmapPaletteTypeCustom))) {
        return false;
    }
    if (FAILED(converter->GetSize(&width, &height)) || width == 0 || height == 0) {
        return false;
    }
    rgba.resize(static_cast<std::size_t>(width) * static_cast<std::size_t>(height) * 4u);
    const UINT stride = width * 4u;
    if (FAILED(converter->CopyPixels(nullptr, stride, static_cast<UINT>(rgba.size()), rgba.data()))) {
        return false;
    }
    return true;
}

bool parse_atlas_json(const std::filesystem::path& path, UINT atlas_w, UINT atlas_h) {
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        return false;
    }
    std::string text((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    const auto icons_pos = text.find("\"icons\"");
    if (icons_pos == std::string::npos) {
        return false;
    }
    std::size_t cursor = text.find('{', icons_pos);
    if (cursor == std::string::npos) {
        return false;
    }
    ++cursor;
    int found = 0;
    while (cursor < text.size()) {
        while (cursor < text.size() && (text[cursor] == ' ' || text[cursor] == '\n' || text[cursor] == '\r' || text[cursor] == '\t' ||
                                        text[cursor] == ',')) {
            ++cursor;
        }
        if (cursor >= text.size() || text[cursor] == '}') {
            break;
        }
        if (text[cursor] != '"') {
            break;
        }
        const auto key_end = text.find('"', cursor + 1);
        if (key_end == std::string::npos) {
            break;
        }
        const std::string key = text.substr(cursor + 1, key_end - cursor - 1);
        cursor = text.find('{', key_end);
        if (cursor == std::string::npos) {
            break;
        }
        const auto obj_end = text.find('}', cursor);
        if (obj_end == std::string::npos) {
            break;
        }
        const std::string obj = text.substr(cursor, obj_end - cursor + 1);
        int x = 0;
        int y = 0;
        int w = 0;
        int h = 0;
        auto read_field = [&](const char* name, int& out) {
            const std::string needle = std::string("\"") + name + "\"";
            const auto at = obj.find(needle);
            if (at == std::string::npos) {
                return false;
            }
            const auto colon = obj.find(':', at + needle.size());
            if (colon == std::string::npos) {
                return false;
            }
            out = std::atoi(obj.c_str() + colon + 1);
            return true;
        };
        if (!read_field("x", x) || !read_field("y", y) || !read_field("w", w) || !read_field("h", h)) {
            cursor = obj_end + 1;
            continue;
        }
        const auto icon = icon_from_key(key);
        if (icon && w > 0 && h > 0 && atlas_w > 0 && atlas_h > 0) {
            GlyphUv& uv = glyph_uv[static_cast<std::size_t>(*icon)];
            uv.uv0 = ImVec2(static_cast<float>(x) / static_cast<float>(atlas_w), static_cast<float>(y) / static_cast<float>(atlas_h));
            uv.uv1 = ImVec2(
                static_cast<float>(x + w) / static_cast<float>(atlas_w), static_cast<float>(y + h) / static_cast<float>(atlas_h));
            uv.ok = true;
            ++found;
        }
        cursor = obj_end + 1;
    }
    return found > 0;
}

}  // namespace

bool load_icon_atlas(ID3D11Device* device, const std::filesystem::path& atlas_png) {
    unload_icon_atlas();
    if (device == nullptr || atlas_png.empty()) {
        return false;
    }
    const auto atlas_json = atlas_png.parent_path() / "atlas.json";
    std::vector<std::uint8_t> rgba;
    UINT width = 0;
    UINT height = 0;
    if (!decode_png_rgba(atlas_png, rgba, width, height)) {
        return false;
    }
    if (!parse_atlas_json(atlas_json, width, height)) {
        return false;
    }

    D3D11_TEXTURE2D_DESC desc{};
    desc.Width = width;
    desc.Height = height;
    desc.MipLevels = 1;
    desc.ArraySize = 1;
    desc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    desc.SampleDesc.Count = 1;
    desc.Usage = D3D11_USAGE_IMMUTABLE;
    desc.BindFlags = D3D11_BIND_SHADER_RESOURCE;

    D3D11_SUBRESOURCE_DATA upload{};
    upload.pSysMem = rgba.data();
    upload.SysMemPitch = width * 4u;

    Microsoft::WRL::ComPtr<ID3D11Texture2D> texture;
    if (FAILED(device->CreateTexture2D(&desc, &upload, &texture))) {
        unload_icon_atlas();
        return false;
    }
    D3D11_SHADER_RESOURCE_VIEW_DESC srv_desc{};
    srv_desc.Format = desc.Format;
    srv_desc.ViewDimension = D3D11_SRV_DIMENSION_TEXTURE2D;
    srv_desc.Texture2D.MipLevels = 1;
    if (FAILED(device->CreateShaderResourceView(texture.Get(), &srv_desc, &atlas_srv))) {
        unload_icon_atlas();
        return false;
    }
    atlas_loaded = true;
    return true;
}

void unload_icon_atlas() {
    atlas_srv.Reset();
    for (GlyphUv& uv : glyph_uv) {
        uv = {};
    }
    atlas_loaded = false;
}

bool icon_atlas_ready() {
    return atlas_loaded && atlas_srv != nullptr;
}

bool draw_icon_atlas(ImDrawList* draw, Icon icon, ImVec2 min, ImVec2 max, ImU32 tint) {
    if (!icon_atlas_ready() || draw == nullptr) {
        return false;
    }
    const auto index = static_cast<std::size_t>(icon);
    if (static_cast<int>(icon) < 0 || static_cast<int>(icon) >= kIconCount || !glyph_uv[index].ok) {
        return false;
    }
    const GlyphUv& uv = glyph_uv[index];
    draw->AddImage(static_cast<ImTextureID>(reinterpret_cast<std::uintptr_t>(atlas_srv.Get())), min, max, uv.uv0, uv.uv1, tint);
    return true;
}

}  // namespace btapp
