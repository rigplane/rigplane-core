"""Hamlib RIG_MODE bits supported by the rigctld server."""

HAMLIB_MODE_MASKS: dict[str, int] = {
    "AM": 0x001,
    "CW": 0x002,
    "USB": 0x004,
    "LSB": 0x008,
    "RTTY": 0x010,
    "FM": 0x020,
    "WFM": 0x040,
    "CWR": 0x080,
    "RTTYR": 0x100,
    "PKTLSB": 0x400,
    "PKTUSB": 0x800,
}
