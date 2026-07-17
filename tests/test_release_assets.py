import hashlib
import json
from pathlib import Path


def test_model_manifests_match_bundled_assets():
    root = Path(__file__).resolve().parents[1]
    manifests = list((root / "models").glob("*/manifest.json"))

    assert manifests
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        model_path = manifest_path.parent / manifest["file"]
        assert model_path.is_file()
        assert hashlib.sha256(model_path.read_bytes()).hexdigest() == manifest["sha256"]
        assert (manifest_path.parent / "LICENSE").is_file()