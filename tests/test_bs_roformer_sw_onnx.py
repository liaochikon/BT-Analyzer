"""Contract and checkpoint parsing for the BS-RoFormer SW exporter."""

import pytest

from bs_roformer_sw_onnx.contract import STEMS, contract_document, time_frames
from bs_roformer_sw_onnx.export_model import (
    extract_state_dict,
    load_config,
    model_kwargs,
    resolve_chunk_samples,
)


class _Weight:
    def __init__(self) -> None:
        self.shape = (2, 2)


def test_contract_matches_the_cpp_graph():
    document = contract_document(588800)

    assert time_frames(588800) == 1151
    assert document["chunk_samples"] == 588800
    assert document["time_frames"] == 1151
    assert document["input"]["names"] == ["spec_real", "spec_imag"]
    assert document["input"]["shape"] == [1, 2, 1025, 1151]
    assert document["output"]["names"] == ["out_spec_real", "out_spec_imag"]
    assert document["output"]["shape"] == [1, 6, 2, 1025, 1151]
    assert document["stems"] == list(STEMS)
    assert document["stems"] == ["bass", "drums", "other", "vocals", "guitar", "piano"]


def test_chunk_length_must_be_a_hop_multiple():
    with pytest.raises(ValueError):
        time_frames(176400)


def test_config_tuple_and_kwargs_drop_unused_fields(tmp_path):
    path = tmp_path / "model.yaml"
    path.write_text(
        "audio:\n  chunk_size: 588800\nmodel:\n  dim: 32\n  dim_freqs_in: 1025\n"
        "  freqs_per_bands: !!python/tuple\n  - 2\n  - 1023\n"
        "training:\n  instruments: [bass, drums, other, vocals, guitar, piano]\n",
        encoding="utf-8",
    )
    config = load_config(path)
    kwargs = model_kwargs(config["model"], {"dim", "freqs_per_bands", "flash_attn", "use_torch_checkpoint"})

    assert config["model"]["freqs_per_bands"] == (2, 1023)
    assert "dim_freqs_in" not in kwargs
    assert kwargs["flash_attn"] is False
    assert kwargs["use_torch_checkpoint"] is False
    assert resolve_chunk_samples(config, None) == 588800
    assert resolve_chunk_samples(config, 176128) == 176128


def test_state_dict_strips_wrapper_prefixes():
    blob = {"state_dict": {"model.module.band_split.weight": _Weight(), "mask_estimators.0.bias": _Weight()}}

    state = extract_state_dict(blob)

    assert set(state) == {"band_split.weight", "mask_estimators.0.bias"}


def test_export_writes_the_fixed_spectrogram_graph(tmp_path):
    pytest.importorskip("torch")
    pytest.importorskip("einops")
    pytest.importorskip("rotary_embedding_torch")
    pytest.importorskip("beartype")
    onnx = pytest.importorskip("onnx")
    import torch

    from bs_roformer_sw_onnx.export_model import build_spectrogram_exporter, export_bs_roformer_sw
    from bs_roformer_sw_onnx.vendor.bs_roformer import DEFAULT_FREQS_PER_BANDS, BSRoformer

    model = BSRoformer(
        dim=32,
        depth=1,
        stereo=True,
        num_stems=6,
        time_transformer_depth=1,
        freq_transformer_depth=1,
        linear_transformer_depth=0,
        freqs_per_bands=DEFAULT_FREQS_PER_BANDS,
        dim_head=8,
        heads=4,
        flash_attn=False,
        mask_estimator_depth=1,
        mlp_expansion_factor=1,
    )
    checkpoint = tmp_path / "tiny.ckpt"
    torch.save({"state_dict": model.state_dict()}, checkpoint)
    bands = "\n".join(f"    - {band}" for band in DEFAULT_FREQS_PER_BANDS)
    config = tmp_path / "tiny.yaml"
    config.write_text(
        "audio:\n  chunk_size: 512\nmodel:\n  dim: 32\n  depth: 1\n  stereo: true\n  num_stems: 6\n"
        "  time_transformer_depth: 1\n  freq_transformer_depth: 1\n  linear_transformer_depth: 0\n"
        "  dim_head: 8\n  heads: 4\n  flash_attn: true\n  dim_freqs_in: 1025\n"
        "  stft_n_fft: 2048\n  stft_hop_length: 512\n  stft_win_length: 2048\n"
        "  mask_estimator_depth: 1\n  mlp_expansion_factor: 1\n"
        f"  freqs_per_bands: !!python/tuple\n{bands}\n"
        "training:\n  instruments: [bass, drums, other, vocals, guitar, piano]\n",
        encoding="utf-8",
    )

    model.eval()
    model_path, contract_path = export_bs_roformer_sw(tmp_path / "out.onnx", checkpoint, config)
    graph = onnx.load(model_path, load_external_data=False).graph

    def static_shape(value):
        return [dim.dim_value for dim in value.type.tensor_type.shape.dim]

    assert [item.name for item in graph.input] == ["spec_real", "spec_imag"]
    assert [item.name for item in graph.output] == ["out_spec_real", "out_spec_imag"]
    op_types = {node.op_type for node in graph.node}
    attention_equations = [
        attr.s.decode()
        for node in graph.node
        if node.op_type == "Einsum"
        for attr in node.attribute
        if attr.name == "equation" and b"i j" in attr.s
    ]
    assert "MatMul" in op_types
    assert attention_equations == []
    assert static_shape(graph.input[0]) == [1, 2, 1025, 2]
    assert static_shape(graph.output[0]) == [1, 6, 2, 1025, 2]
    assert contract_path.is_file()

    audio = torch.randn(1, 2, 4096)
    window = model.stft_window_fn()
    spec = torch.stft(
        audio.reshape(2, -1),
        n_fft=2048,
        hop_length=512,
        win_length=2048,
        window=window,
        center=True,
        return_complex=True,
    )
    with torch.no_grad():
        reference = model(audio)
        masked_real, masked_imag = build_spectrogram_exporter(model)(spec.real.unsqueeze(0), spec.imag.unsqueeze(0))
    masked = torch.complex(masked_real, masked_imag)
    masked[:, :, :, 0, :] = 0
    restored = torch.istft(
        masked.reshape(12, 1025, masked.shape[-1]),
        n_fft=2048,
        hop_length=512,
        win_length=2048,
        window=window,
        center=True,
        length=4096,
    ).reshape(1, 6, 2, 4096)
    torch.testing.assert_close(restored, reference, atol=1e-4, rtol=1e-4)

    real = torch.randn(1, 2, 1025, 2)
    imag = torch.randn(1, 2, 1025, 2)
    with torch.no_grad():
        exported_real, exported_imag = build_spectrogram_exporter(model)(real, imag)
    ort = pytest.importorskip("onnxruntime")
    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    got_real, got_imag = session.run(None, {"spec_real": real.numpy(), "spec_imag": imag.numpy()})
    torch.testing.assert_close(torch.from_numpy(got_real), exported_real, atol=1e-4, rtol=1e-4)
    torch.testing.assert_close(torch.from_numpy(got_imag), exported_imag, atol=1e-4, rtol=1e-4)
