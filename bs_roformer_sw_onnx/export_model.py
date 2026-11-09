"""Export a local BS-RoFormer SW checkpoint to the ONNX graph bt-app loads.

Export is a development step. It needs PyTorch and does not download weights.
Inference in the C++ app needs only the ONNX file.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import yaml

from bs_roformer_sw_onnx.contract import (
    FREQ_BINS,
    HOP_LENGTH,
    INPUT_IMAG,
    INPUT_REAL,
    N_FFT,
    OUTPUT_IMAG,
    OUTPUT_REAL,
    STEMS,
    time_frames,
    write_contract,
)

_STATE_KEYS = ("state_dict", "state", "model")


class _TupleLoader(yaml.SafeLoader):
    """Read the SW yaml, including its ``!!python/tuple`` bands, without executing tags."""


def _construct_tuple(loader: yaml.SafeLoader, node: yaml.nodes.Node) -> tuple:
    return tuple(loader.construct_sequence(node))


_TupleLoader.add_constructor("tag:yaml.org,2002:python/tuple", _construct_tuple)


def load_config(path: Path | str) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    loaded = yaml.load(text, Loader=_TupleLoader)
    if not isinstance(loaded, dict):
        raise RuntimeError(f"{path} is not a BS-RoFormer config")
    return loaded


def model_kwargs(model_config: dict, parameter_names: set[str]) -> dict:
    """Keep constructor arguments. The SW yaml also carries ``dim_freqs_in``."""
    kwargs = {}
    for key, value in model_config.items():
        if key not in parameter_names:
            continue
        if key == "freqs_per_bands" and not isinstance(value, tuple):
            value = tuple(value)
        kwargs[key] = value
    kwargs["flash_attn"] = False
    kwargs["use_torch_checkpoint"] = False
    return kwargs


def _looks_like_state_dict(value: object) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    sample = next(iter(value.values()))
    return hasattr(sample, "shape")


def extract_state_dict(blob: object) -> dict:
    current = blob
    for _ in range(4):
        if not isinstance(current, dict):
            break
        nested = next((current[key] for key in _STATE_KEYS if _looks_like_state_dict(current.get(key))), None)
        if nested is None:
            break
        current = nested
    if not _looks_like_state_dict(current):
        raise RuntimeError("checkpoint does not contain a state dict")
    cleaned = {}
    for key, value in current.items():
        name = str(key)
        while name.startswith("model.") or name.startswith("module."):
            name = name[6:] if name.startswith("model.") else name[7:]
        cleaned[name] = value
    return cleaned


def resolve_chunk_samples(config: dict, override: int | None) -> int:
    if override is not None:
        samples = int(override)
    else:
        audio = config.get("audio") or {}
        if "chunk_size" not in audio:
            raise RuntimeError("config has no audio.chunk_size; pass --chunk-samples")
        samples = int(audio["chunk_size"])
    time_frames(samples)
    return samples


def _check_architecture(kwargs: dict, config: dict) -> None:
    if not kwargs.get("stereo", False):
        raise RuntimeError("bt-app expects a stereo BS-RoFormer")
    if int(kwargs.get("num_stems", 0)) != len(STEMS):
        raise RuntimeError(f"bt-app expects {len(STEMS)} stems")
    if int(kwargs.get("stft_n_fft", N_FFT)) != N_FFT or int(kwargs.get("stft_hop_length", HOP_LENGTH)) != HOP_LENGTH:
        raise RuntimeError("bt-app STFT is fixed at n_fft 2048 and hop 512")
    bands = kwargs.get("freqs_per_bands")
    if bands is not None and sum(bands) != FREQ_BINS:
        raise RuntimeError(f"freqs_per_bands must sum to {FREQ_BINS}")
    instruments = tuple((config.get("training") or {}).get("instruments") or STEMS)
    if instruments != STEMS:
        raise RuntimeError(f"stem order must be {', '.join(STEMS)}")


def build_spectrogram_exporter(inner):
    """Wrap a loaded BSRoformer so the graph is real and imaginary tensors only."""
    import torch
    from torch import nn

    from einops import rearrange

    class SpectrogramExporter(nn.Module):
        def __init__(self, network) -> None:
            super().__init__()
            self.inner = network

        def forward(self, spec_real: torch.Tensor, spec_imag: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
            network = self.inner
            stft = torch.stack((spec_real, spec_imag), dim=-1)
            stft = rearrange(stft, "b s f t c -> b (f s) t c")
            features = rearrange(stft, "b f t c -> b t (f c)")
            features = network.band_split(features)
            stored: list[torch.Tensor | None] = [None] * len(network.layers)
            for index, block in enumerate(network.layers):
                if len(block) == 3:
                    linear_transformer, time_transformer, freq_transformer = block
                    features, packed = _rearrange_pack(features, "b * d")
                    features = linear_transformer(features)
                    features = _rearrange_unpack(features, packed, "b * d")
                else:
                    time_transformer, freq_transformer = block
                if network.skip_connection:
                    for earlier in range(index):
                        features = features + stored[earlier]
                features = rearrange(features, "b t f d -> b f t d")
                features, packed = _rearrange_pack(features, "* t d")
                features = time_transformer(features)
                features = _rearrange_unpack(features, packed, "* t d")
                features = rearrange(features, "b f t d -> b t f d")
                features, packed = _rearrange_pack(features, "* f d")
                features = freq_transformer(features)
                features = _rearrange_unpack(features, packed, "* f d")
                if network.skip_connection:
                    stored[index] = features
            features = network.final_norm(features)
            mask = torch.stack([head(features) for head in network.mask_estimators], dim=1)
            mask = rearrange(mask, "b n t (f c) -> b n f t c", c=2)
            spec = rearrange(stft, "b f t c -> b 1 f t c")
            real = spec[..., 0] * mask[..., 0] - spec[..., 1] * mask[..., 1]
            imag = spec[..., 0] * mask[..., 1] + spec[..., 1] * mask[..., 0]
            real = rearrange(real, "b n (f s) t -> b n s f t", s=network.audio_channels)
            imag = rearrange(imag, "b n (f s) t -> b n s f t", s=network.audio_channels)
            return real.contiguous(), imag.contiguous()

    return SpectrogramExporter(inner)


def _rearrange_pack(value, pattern: str):
    from einops import pack

    packed, shape = pack([value], pattern)
    return packed, shape


def _rearrange_unpack(value, shape, pattern: str):
    from einops import unpack

    return unpack(value, shape, pattern)[0]


def export_bs_roformer_sw(
    output: Path | str,
    checkpoint: Path | str,
    config: Path | str,
    chunk_samples: int | None = None,
) -> tuple[Path, Path]:
    """Write ``output`` and a sibling ``.contract.json``. Return both paths."""
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError(
            "Export needs PyTorch, PyYAML, einops, rotary-embedding-torch, beartype, and onnx. "
            "See requirements-bs-roformer-onnx.txt."
        ) from exc

    from bs_roformer_sw_onnx.vendor.bs_roformer import BSRoformer

    loaded = load_config(config)
    samples = resolve_chunk_samples(loaded, chunk_samples)
    frames = time_frames(samples)
    accepted = set(inspect.signature(BSRoformer.__init__).parameters)
    kwargs = model_kwargs(loaded.get("model") or {}, accepted)
    _check_architecture(kwargs, loaded)

    model = BSRoformer(**kwargs)
    weights = extract_state_dict(torch.load(Path(checkpoint), map_location="cpu", weights_only=False))
    missing, unexpected = model.load_state_dict(weights, strict=False)
    real_missing = [key for key in missing if "rotary" not in key and not key.endswith("inv_freq")]
    if real_missing or unexpected:
        detail = []
        if real_missing:
            detail.append("missing " + ", ".join(real_missing[:8]))
        if unexpected:
            detail.append("unexpected " + ", ".join(unexpected[:8]))
        raise RuntimeError("checkpoint does not match the config: " + "; ".join(detail))
    model.eval()

    destination = Path(output)
    if destination.suffix.lower() != ".onnx":
        destination = destination / "bs_roformer_sw.onnx"
    destination.parent.mkdir(parents=True, exist_ok=True)

    exporter = build_spectrogram_exporter(model).eval()
    example_real = torch.zeros(1, 2, FREQ_BINS, frames)
    example_imag = torch.zeros(1, 2, FREQ_BINS, frames)
    export_kwargs = dict(
        input_names=[INPUT_REAL, INPUT_IMAG],
        output_names=[OUTPUT_REAL, OUTPUT_IMAG],
        opset_version=18,
        do_constant_folding=True,
        dynamo=False,
    )
    with torch.no_grad():
        try:
            torch.onnx.export(exporter, (example_real, example_imag), str(destination), **export_kwargs)
        except TypeError:
            export_kwargs.pop("dynamo")
            torch.onnx.export(exporter, (example_real, example_imag), str(destination), **export_kwargs)
    contract_path = write_contract(destination.with_suffix(".contract.json"), samples)
    return destination, contract_path


def write_fp16_compute(source: Path | str, destination: Path | str) -> Path:
    """Write the same graph with float16 weights and compute. Inputs and outputs stay float32."""
    try:
        import onnx
        from onnxconverter_common import float16
    except ImportError as exc:
        raise RuntimeError(
            "fp16 export needs onnx and onnxconverter-common. See requirements-bs-roformer-onnx.txt."
        ) from exc

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    model = onnx.load(str(source))
    converted = float16.convert_float_to_float16(model, keep_io_types=True)
    graph_outputs = {value.name for value in converted.graph.output}
    for node in converted.graph.node:
        if node.op_type != "Cast":
            continue
        cast_to = next(attr for attr in node.attribute if attr.name == "to")
        if cast_to.i != onnx.TensorProto.FLOAT:
            continue
        if any(name in graph_outputs for name in node.output):
            continue
        # The fp32 graph casts between float tensors. Leaving those as float32
        # pulls the attention scores back to a 2.5GB matrix.
        cast_to.i = onnx.TensorProto.FLOAT16
    for value in list(converted.graph.input) + list(converted.graph.output):
        elem = value.type.tensor_type.elem_type
        if elem != onnx.TensorProto.FLOAT:
            raise RuntimeError(f"{value.name} must stay float32, got elem_type {elem}")
    onnx.save(converted, str(destination))
    return destination
