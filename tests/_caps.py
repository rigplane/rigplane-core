# A broad Icom capability tag superset shared across web-handler tests to
# enable capability gates without duplicating a full list per test. NOT a
# literal, exact copy of any one profile's declared capabilities — e.g. it
# carries "repeater_tone"/"tsql" which the real IC-7610 profile omits
# (MOR-661). The authoritative exact-match check for IC-7610's declared
# capability set is ``tests/test_rig_ic7610.py::TestProfileParity::
# test_capabilities_exact`` against the real TOML-loaded profile — update
# that test, not (only) this fixture, when ic7610.toml's [capabilities]
# list changes.
FULL_ICOM_CAPS: frozenset[str] = frozenset(
    {
        "audio",
        "scope",
        "meters",
        "power_control",
        "af_level",
        "rf_gain",
        "squelch",
        "cw",
        "attenuator",
        "preamp",
        "antenna",
        "rx_antenna",
        "system_settings",
        "dual_watch",
        "tuner",
        "data_mode",
        "nb",
        "nr",
        "ip_plus",
        "digisel",
        "digisel_shift",
        "vox",
        "compressor",
        "break_in",
        "notch",
        "apf",
        "repeater_tone",
        "tsql",
        "main_sub_tracking",
        "ssb_tx_bw",
        "filter_width",
        "filter_shape",
        "tx",
        "dual_rx",
        "agc",
        "tuning_step",
        "band_edge",
        "xfc",
        "pbt",
        "rit",
        "monitor",
    }
)
