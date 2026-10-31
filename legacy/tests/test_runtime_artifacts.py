import numpy as np

from core import disk_cache, runtime_artifacts


class BakedNode:
    def __init__(self, node_tag, signature, outputs=None):
        self.node_tag = node_tag
        self.baked_signature = signature
        self.cached_outputs = outputs or {}
        self.is_baked = bool(outputs)


def test_artifact_registry_deduplicates_content_and_releases_unreferenced_entries():
    runtime_artifacts.clear()
    values = np.arange(8, dtype=np.float32)
    first = BakedNode("node_1", "same", {"node_1_out_audio::AUDIO": values})
    second = BakedNode("node_2", "same", {"node_2_out_audio::AUDIO": values})

    first_ref = runtime_artifacts.capture({"node_1": first})
    second_ref = runtime_artifacts.capture({"node_2": second})

    assert runtime_artifacts.total_bytes() == values.nbytes
    runtime_artifacts.retain(first_ref + second_ref)
    assert runtime_artifacts.total_bytes() == values.nbytes
    runtime_artifacts.retain(())
    assert runtime_artifacts.total_bytes() == 0


def test_artifact_restore_falls_back_to_disk_after_ram_eviction(tmp_path, monkeypatch):
    runtime_artifacts.clear()
    monkeypatch.setattr(disk_cache, "CACHE_ROOT", str(tmp_path / "cache"))
    values = np.arange(6, dtype=np.float32)
    assert disk_cache.save(BakedNode.__name__, "disk-only", {
        "out_audio::AUDIO": values,
    })
    old = BakedNode("node_1", "disk-only")
    refs = runtime_artifacts.capture({"node_1": old})
    restored = BakedNode("node_1", None)

    count = runtime_artifacts.restore({"node_1": restored}, refs)

    assert count == 1
    assert restored.is_baked is True
    assert restored.baked_signature == "disk-only"
    assert np.array_equal(restored.cached_outputs["node_1_out_audio::AUDIO"], values)


def test_artifact_capture_excludes_stale_outputs():
    runtime_artifacts.clear()
    node = BakedNode(
        "node_1", "obsolete", {"node_1_out_audio::AUDIO": np.ones(4, np.float32)})

    refs = runtime_artifacts.capture({"node_1": node}, {"node_1"})

    assert refs == ()
    assert runtime_artifacts.total_bytes() == 0