"""Beat Grid node: the _scalar unwrap guard that turns tempo/offset packets into plain floats."""

import numpy as np

from core.base_node import DataType, DataPacket


def _pkt(arr):
    return DataPacket(arr, DataType.TEMPO)


def test_scalar_none_or_empty_returns_default(node_registry):
    BeatGrid = node_registry["Beat Grid Output"]
    assert BeatGrid._scalar(None, 120.0) == 120.0
    assert BeatGrid._scalar(_pkt(None), 99.0) == 99.0
    assert BeatGrid._scalar(_pkt(np.zeros(0, dtype=np.float32)), 88.0) == 88.0   # empty -> default


def test_scalar_unwraps_0d_and_1d_arrays(node_registry):
    BeatGrid = node_registry["Beat Grid Output"]
    assert BeatGrid._scalar(_pkt(np.asarray(120.0, dtype=np.float32)), 0.0) == 120.0   # 0-d
    assert BeatGrid._scalar(_pkt(np.asarray([90.0], dtype=np.float32)), 0.0) == 90.0   # (1,)
    # A longer array takes the first element (defensive; scalar packets are 0-d/(1,) by contract).
    assert BeatGrid._scalar(_pkt(np.asarray([75.0, 5.0], dtype=np.float32)), 0.0) == 75.0
