"""Yaesu CAT command formatter and parser.

Compile-once template engine for Yaesu CAT protocol text commands.
Templates use Python format-string style placeholders, e.g. ``FA{freq:09d};``.

Supported placeholders
----------------------
{freq:09d}    Frequency in Hz, 9 digits zero-padded
{freq:03d}    Frequency index, 3 digits zero-padded
{mode}        Mode code, single character (e.g. '2' for USB)
{mode:02d}    Mode code, 2 digits zero-padded (e.g. RX function)
{raw:03d}     Meter raw value, 3 digits zero-padded
{level:03d}   Level value, 3 digits zero-padded (0-255)
{level:02d}   Level value, 2 digits zero-padded (0-15)
{state}       Binary state character ('0' or '1')
{state:03d}   Binary state, 3 digits zero-padded (e.g. manual notch)
{sign}        Sign character ('+' or '-')
{offset:04d}  Frequency offset, 4 digits zero-padded
{value}       Generic single-character value
{value:02d}   Generic value, 2 digits zero-padded
{vfo}         VFO selector, single character ('0' or '1')
{band:02d}    Band index, 2 digits zero-padded
{wpm:03d}     CW speed in WPM, 3 digits zero-padded
{idx:02d}     Index value, 2 digits zero-padded
{delay:04d}   Delay in milliseconds, 4 digits zero-padded
{head}        Head selector, single character
{watts:03d}   Power in watts, 3 digits zero-padded
{model:04d}   Radio model ID, 4 digits zero-padded
{rx}          RX clarifier state, single character
{tx}          TX clarifier state, single character
{pad:03d}     Padding zeros, 3 digits
{type:02d}    Type/code value, 2 digits zero-padded
{mem}         CW message text (write-only)

Usage
-----
    formatted = format_command("FA{freq:09d};", freq=14074000)
    # → "FA014074000;"

    parser = CatCommandParser("FA{freq:09d};")
    result = parser.parse("FA014074000;")
    # → {"freq": 14074000}
"""

from __future__ import annotations

import re
import string
from typing import Any

__all__ = [
    "CatFormatError",
    "CatParseError",
    "CatCommandParser",
    "format_command",
]

# ---------------------------------------------------------------------------
# Allowed placeholder names
# ---------------------------------------------------------------------------

_ALLOWED_PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "freq",
        "mode",
        "raw",
        "level",
        "state",
        "sign",
        "offset",
        "value",
        "vfo",
        "band",
        "wpm",
        "code",
        "idx",
        "delay",
        "head",
        "watts",
        "model",
        "rx",
        "tx",
        "pad",
        "type",
        "mem",
        "src",
        "func",
        "val",
        "main",
        "sub",
    }
)

# ---------------------------------------------------------------------------
# Placeholder → regex fragment mapping
# ---------------------------------------------------------------------------

# Maps placeholder name (optionally with format spec) to a named regex group.
# Each entry: (name_prefix, regex_pattern, type_converter)
_PLACEHOLDER_REGEX: dict[str, tuple[str, Any]] = {
    "freq:09d": (r"(?P<freq>\d{9})", int),
    "freq:03d": (r"(?P<freq>\d{3})", int),
    "raw:03d": (r"(?P<raw>\d{3})", int),
    "level:03d": (r"(?P<level>\d{3})", int),
    "level:02d": (r"(?P<level>\d{2})", int),
    "offset:04d": (r"(?P<offset>\d{4})", int),
    "mode": (r"(?P<mode>.)", str),
    "mode:02d": (r"(?P<mode>\d{2})", int),
    "state": (r"(?P<state>.)", str),
    "state:03d": (r"(?P<state>\d{3})", int),
    "sign": (r"(?P<sign>[+\-])", str),
    "value": (r"(?P<value>.)", str),
    "value:02d": (r"(?P<value>\d{2})", int),
    "vfo": (r"(?P<vfo>.)", str),
    "band:02d": (r"(?P<band>\d{2})", int),
    "wpm:03d": (r"(?P<wpm>\d{3})", int),
    "code:03d": (r"(?P<code>\d{3})", int),
    "idx:02d": (r"(?P<idx>\d{2})", int),
    "delay:04d": (r"(?P<delay>\d{4})", int),
    "head": (r"(?P<head>.)", str),
    "watts:03d": (r"(?P<watts>\d{3})", int),
    "model:04d": (r"(?P<model>\d{4})", int),
    "rx": (r"(?P<rx>.)", str),
    "tx": (r"(?P<tx>.)", str),
    "pad:03d": (r"(?P<pad>\d{3})", int),
    "type": (r"(?P<type>.)", str),
    "type:02d": (r"(?P<type>\d{2})", int),
    "src": (r"(?P<src>.)", str),
    "func": (r"(?P<func>.)", str),
    "val": (r"(?P<val>.)", str),
    "val:04d": (r"(?P<val>\d{4})", int),
    "mem": (r"(?P<mem>.)", str),
    "main:03d": (r"(?P<main>\d{3})", int),
    "sub:03d": (r"(?P<sub>\d{3})", int),
}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CatFormatError(ValueError):
    """Raised when a CAT command template cannot be formatted."""

    def __init__(self, template: str, params: dict[str, Any], reason: str) -> None:
        self.template = template
        self.params = params
        self.reason = reason
        super().__init__(f"Format error for {template!r} with {params!r}: {reason}")


class CatParseError(ValueError):
    """Raised when a CAT response cannot be parsed against a template."""

    def __init__(self, template: str, response: str, reason: str) -> None:
        self.template = template
        self.response = response
        self.reason = reason
        super().__init__(f"Parse error for {template!r} against {response!r}: {reason}")


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------


def _extract_field_names(template: str) -> list[str]:
    """Return field names referenced in a format template (without format spec)."""
    formatter = string.Formatter()
    names: list[str] = []
    for _, field_name, _, _ in formatter.parse(template):
        if field_name is not None:
            # Strip index/attribute access, e.g. "freq" from "freq:09d"
            base = field_name.split(".")[0].split("[")[0]
            if base:
                names.append(base)
    return names


def format_command(template: str, **kwargs: Any) -> str:
    """Format a CAT command template with the given keyword arguments.

    Parameters
    ----------
    template:
        A CAT command template string, e.g. ``"FA{freq:09d};"``
    **kwargs:
        Values for the placeholders in the template.

    Returns
    -------
    str
        The formatted CAT command string.

    Raises
    ------
    CatFormatError
        If an unknown placeholder name is used or formatting fails.
    """
    field_names = _extract_field_names(template)
    unknown = [n for n in field_names if n not in _ALLOWED_PLACEHOLDERS]
    if unknown:
        raise CatFormatError(
            template,
            kwargs,
            f"Unknown placeholder(s): {', '.join(sorted(unknown))}",
        )
    try:
        return template.format(**kwargs)
    except (KeyError, ValueError, TypeError) as exc:
        raise CatFormatError(template, kwargs, str(exc)) from exc


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _build_regex(template: str) -> tuple[re.Pattern[str], dict[str, Any]]:
    """Compile a regex pattern and type-converter map from a parse template.

    Literal characters in the template become literal regex matches; each
    ``{name}`` or ``{name:spec}`` placeholder is replaced by a named capture
    group according to ``_PLACEHOLDER_REGEX``.
    """
    converters: dict[str, Any] = {}
    # We build the regex by walking the template character-by-character,
    # replacing {…} placeholders with named groups.
    regex_parts: list[str] = []
    i = 0
    n = len(template)
    while i < n:
        ch = template[i]
        if ch == "{":
            # Find the closing brace
            j = template.index("}", i)
            placeholder = template[i + 1 : j]  # e.g. "freq:09d" or "mode"
            # Determine the base name (before the colon)
            base_name = placeholder.split(":")[0]
            if base_name not in _ALLOWED_PLACEHOLDERS:
                raise ValueError(
                    f"Unknown placeholder {{{placeholder}}} in parse template {template!r}"
                )
            # Look up the regex / converter for this placeholder+spec
            if placeholder in _PLACEHOLDER_REGEX:
                group_pattern, converter = _PLACEHOLDER_REGEX[placeholder]
            elif base_name in _PLACEHOLDER_REGEX:
                group_pattern, converter = _PLACEHOLDER_REGEX[base_name]
            else:
                raise ValueError(
                    f"No regex mapping for placeholder {{{placeholder}}} in template {template!r}"
                )
            regex_parts.append(group_pattern)
            converters[base_name] = converter
            i = j + 1
        else:
            regex_parts.append(re.escape(ch))
            i += 1

    pattern = "".join(regex_parts)
    compiled = re.compile(r"^" + pattern + r"$")
    return compiled, converters


class CatCommandParser:
    """Compile-once parser for a Yaesu CAT response template.

    Parameters
    ----------
    parse_template:
        A CAT response template string, e.g. ``"FA{freq:09d};"`` or
        ``"SM{state}{raw:03d};"``

    Example
    -------
        parser = CatCommandParser("FA{freq:09d};")
        result = parser.parse("FA014074000;")
        # → {"freq": 14074000}
    """

    def __init__(self, parse_template: str) -> None:
        self.template = parse_template
        self._pattern, self._converters = _build_regex(parse_template)

    def parse(self, response: str) -> dict[str, Any]:
        """Parse a CAT response string against the compiled template.

        Parameters
        ----------
        response:
            The raw CAT response string received from the radio.

        Returns
        -------
        dict[str, Any]
            Extracted field values with appropriate Python types applied
            (e.g. ``freq`` → ``int``, ``mode`` → ``str``).

        Raises
        ------
        CatParseError
            If the response does not match the template pattern.
        """
        m = self._pattern.match(response)
        if m is None:
            raise CatParseError(
                self.template,
                response,
                f"Response does not match pattern {self._pattern.pattern!r}",
            )
        result: dict[str, Any] = {}
        for name, raw_value in m.groupdict().items():
            converter = self._converters.get(name, str)
            result[name] = converter(raw_value)
        return result
