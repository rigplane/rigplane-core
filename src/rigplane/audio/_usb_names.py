"""Name comparisons shared by Windows USB audio selection paths."""

import re


def endpoint_name_key(name: str) -> str:
    """Normalize whitespace, including spaces just inside parentheses."""
    return re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", " ".join(name.split())))


def windows_audio_product(name: str) -> str:
    match = re.fullmatch(r".*\((?:\d+-\s*)?([^()]+)\)", name.strip())
    return match[1].strip() if match else name.strip()


def mme_listed_matches(listed: str, owned: str) -> bool:
    """Allow MME's 31-character listed-name truncation in one direction."""
    listed_key = endpoint_name_key(listed)
    owned_key = endpoint_name_key(owned)
    return listed_key == owned_key or (
        len(listed) == 31 and owned_key.startswith(listed_key)
    )
