"""Name comparisons shared by USB audio selection paths."""

import re

from rigplane.audio.backend import _ALSA_HW_RE

_ALSA_NAME_SUFFIX_RE = re.compile(rf"\s*\({_ALSA_HW_RE.pattern}\)\s*$")


def endpoint_name_key(name: str) -> str:
    """Normalize whitespace and ALSA decorations, retaining the hardware address."""
    name = re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", " ".join(name.split())))
    suffix = _ALSA_NAME_SUFFIX_RE.search(name)
    if suffix is not None:
        card = name[: suffix.start()].partition(":")[0].removesuffix(", USB Audio")
        return f"{card.strip()} ({suffix.group(1)})"
    return name


def mme_listed_matches(listed: str, owned: str) -> bool:
    """Allow MME's 31-character listed-name truncation in one direction."""
    listed_key = endpoint_name_key(listed)
    owned_key = endpoint_name_key(owned)
    return listed_key == owned_key or (
        len(listed) == 31 and owned_key.startswith(listed_key)
    )
