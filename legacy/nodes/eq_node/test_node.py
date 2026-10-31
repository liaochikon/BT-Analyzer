"""EQ node: the X/X_default serialize_params contract (shared convention, exercised here on EQ)."""


def test_serialize_params_captures_scalars(make_node):
    e = make_node("EQ")
    e.low_gain = 3.0
    e.mid_freq = 2500.0
    p = e.serialize_params()
    assert p["low_gain"] == 3.0 and p["mid_freq"] == 2500.0


def test_apply_params_restores_state(make_node):
    e = make_node("EQ")
    e.apply_params({"low_gain": -4.0, "high_freq": 12000.0})
    assert e.low_gain == -4.0 and e.high_freq == 12000.0


def test_eq_declarative_params_preserve_signature_and_mid_q_floor(make_node):
    eq = make_node("EQ")
    eq.apply_params({
        "low_gain": 3.0,
        "low_freq": 120.0,
        "mid_gain": -2.0,
        "mid_freq": 900.0,
        "mid_q": 0.0,
        "high_gain": 1.5,
        "high_freq": 10000.0,
    })

    assert eq.serialize_params()["mid_q"] == 0.1
    assert eq.get_param_signature() == (
        "low=3.00@120.0,mid=-2.00@900.0/0.10,high=1.50@10000.0")
