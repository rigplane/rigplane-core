"""Unified capability constants and known-capability registry.

Every capability tag used by icom-lan rig profiles lives here.
Import constants from this module instead of using bare strings.
"""

__all__ = [
    "KNOWN_CAPABILITIES",
    # Re-export all CAP_* constants for convenient `from .capabilities import CAP_NB`
    "CAP_AUDIO",
    "CAP_DUAL_RX",
    "CAP_DUAL_WATCH",
    "CAP_LAN_DUAL_RX_AUDIO_ROUTING",
    "CAP_AF_LEVEL",
    "CAP_RF_GAIN",
    "CAP_SQUELCH",
    "CAP_ATTENUATOR",
    "CAP_PREAMP",
    "CAP_DIGISEL",
    "CAP_IP_PLUS",
    "CAP_ANTENNA",
    "CAP_RX_ANTENNA",
    "CAP_NB",
    "CAP_NR",
    "CAP_NOTCH",
    "CAP_APF",
    "CAP_TWIN_PEAK",
    "CAP_PBT",
    "CAP_FILTER_WIDTH",
    "CAP_FILTER_SHAPE",
    "CAP_IF_SHIFT",
    "CAP_CONTOUR",
    "CAP_TX",
    "CAP_SPLIT",
    "CAP_VOX",
    "CAP_COMPRESSOR",
    "CAP_MONITOR",
    "CAP_DRIVE_GAIN",
    "CAP_SSB_TX_BW",
    "CAP_CW",
    "CAP_BREAK_IN",
    "CAP_RIT",
    "CAP_XIT",
    "CAP_TUNER",
    "CAP_METERS",
    "CAP_SCOPE",
    "CAP_REPEATER_TONE",
    "CAP_TSQL",
    "CAP_DTCS",
    "CAP_CSQL",
    "CAP_VOICE_TX",
    "CAP_DATA_MODE",
    "CAP_AGC",
    "CAP_POWER_CONTROL",
    "CAP_DIAL_LOCK",
    "CAP_SCAN",
    "CAP_BSR",
    "CAP_MAIN_SUB_TRACKING",
    "CAP_TUNING_STEP",
    "CAP_BAND_EDGE",
    "CAP_XFC",
    "CAP_SYSTEM_SETTINGS",
    "CAP_WEBRTC",
]

# ---------------------------------------------------------------------------
# Capability tag constants — grouped by category
# ---------------------------------------------------------------------------

# Receiver
CAP_AUDIO = "audio"
CAP_DUAL_RX = "dual_rx"
CAP_DUAL_WATCH = "dual_watch"
CAP_AF_LEVEL = "af_level"
CAP_RF_GAIN = "rf_gain"
CAP_SQUELCH = "squelch"

# LAN audio stereo routing via the ``0x1A 05 00 72`` "Phones L/R Mix" CI-V
# sub-command (IC-7610 menu path: Connectors → Phones → L/R Mix).  Declaring
# this capability authorises the web audio broadcaster to (a) force Mix OFF
# once per relay start so the LAN stream stays separated L=MAIN / R=SUB, and
# (b) echo the frontend ``audio_config`` message's ``focus`` / ``split_stereo``
# payload back to the client.  Both paths are dual-RX contract (#787 / #792 /
# #794).  Do NOT add this tag to rigs that don't expose this menu item — IC-
# 9700 also has ``receiver_count=2`` but its menu layout does not include
# ``0x1A 05 00 72``, and FTX-1 runs on Yaesu CAT which has no ``send_civ``
# at all.
CAP_LAN_DUAL_RX_AUDIO_ROUTING = "lan_dual_rx_audio_routing"

# RF front-end
CAP_ATTENUATOR = "attenuator"
CAP_PREAMP = "preamp"
CAP_DIGISEL = "digisel"
CAP_IP_PLUS = "ip_plus"

# Antenna
CAP_ANTENNA = "antenna"
CAP_RX_ANTENNA = "rx_antenna"

# DSP / Noise
CAP_NB = "nb"
CAP_NR = "nr"
CAP_NOTCH = "notch"
CAP_APF = "apf"
CAP_TWIN_PEAK = "twin_peak"

# Filter
CAP_PBT = "pbt"
CAP_FILTER_WIDTH = "filter_width"
CAP_FILTER_SHAPE = "filter_shape"
CAP_IF_SHIFT = "if_shift"
CAP_CONTOUR = "contour"

# TX
CAP_TX = "tx"
CAP_SPLIT = "split"
CAP_VOX = "vox"
CAP_COMPRESSOR = "compressor"
CAP_MONITOR = "monitor"
CAP_DRIVE_GAIN = "drive_gain"
CAP_SSB_TX_BW = "ssb_tx_bw"

# CW
CAP_CW = "cw"
CAP_BREAK_IN = "break_in"

# RIT / XIT
CAP_RIT = "rit"
CAP_XIT = "xit"

# Tuner
CAP_TUNER = "tuner"

# Metering
CAP_METERS = "meters"

# Scope
CAP_SCOPE = "scope"

# Tone
CAP_REPEATER_TONE = "repeater_tone"
CAP_TSQL = "tsql"
CAP_DTCS = "dtcs"
CAP_CSQL = "csql"
CAP_VOICE_TX = "voice_tx"

# Data
CAP_DATA_MODE = "data_mode"

# AGC
CAP_AGC = "agc"

# System
CAP_POWER_CONTROL = "power_control"
CAP_DIAL_LOCK = "dial_lock"
CAP_SCAN = "scan"
CAP_BSR = "bsr"
CAP_MAIN_SUB_TRACKING = "main_sub_tracking"
CAP_TUNING_STEP = "tuning_step"
CAP_BAND_EDGE = "band_edge"
CAP_XFC = "xfc"
CAP_SYSTEM_SETTINGS = "system_settings"

# WebRTC — low-latency audio delivery via browser peer connection (issue #104)
CAP_WEBRTC = "webrtc"

# ---------------------------------------------------------------------------
# Master set — rig_loader rejects any TOML tag not listed here.
# ---------------------------------------------------------------------------

KNOWN_CAPABILITIES: frozenset[str] = frozenset(
    {
        # Receiver
        CAP_AUDIO,
        CAP_DUAL_RX,
        CAP_DUAL_WATCH,
        CAP_LAN_DUAL_RX_AUDIO_ROUTING,
        CAP_AF_LEVEL,
        CAP_RF_GAIN,
        CAP_SQUELCH,
        # RF front-end
        CAP_ATTENUATOR,
        CAP_PREAMP,
        CAP_DIGISEL,
        CAP_IP_PLUS,
        # Antenna
        CAP_ANTENNA,
        CAP_RX_ANTENNA,
        # DSP / Noise
        CAP_NB,
        CAP_NR,
        CAP_NOTCH,
        CAP_APF,
        CAP_TWIN_PEAK,
        # Filter
        CAP_PBT,
        CAP_FILTER_WIDTH,
        CAP_FILTER_SHAPE,
        CAP_IF_SHIFT,
        CAP_CONTOUR,
        # TX
        CAP_TX,
        CAP_SPLIT,
        CAP_VOX,
        CAP_COMPRESSOR,
        CAP_MONITOR,
        CAP_DRIVE_GAIN,
        CAP_SSB_TX_BW,
        # CW
        CAP_CW,
        CAP_BREAK_IN,
        # RIT / XIT
        CAP_RIT,
        CAP_XIT,
        # Tuner
        CAP_TUNER,
        # Metering
        CAP_METERS,
        # Scope
        CAP_SCOPE,
        # Tone
        CAP_REPEATER_TONE,
        CAP_TSQL,
        CAP_DTCS,
        CAP_CSQL,
        CAP_VOICE_TX,
        # Data
        CAP_DATA_MODE,
        # AGC
        CAP_AGC,
        # System
        CAP_POWER_CONTROL,
        CAP_DIAL_LOCK,
        CAP_SCAN,
        CAP_BSR,
        CAP_MAIN_SUB_TRACKING,
        CAP_TUNING_STEP,
        CAP_BAND_EDGE,
        CAP_XFC,
        CAP_SYSTEM_SETTINGS,
        # WebRTC
        CAP_WEBRTC,
    }
)
