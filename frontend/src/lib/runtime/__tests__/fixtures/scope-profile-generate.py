"""Generate scope-profile-caps.json on the mini from the shipped profiles."""
import json

from rigplane.profiles import resolve_radio_profile


def ranges(model):
    return [
        {
            "start": r.start,
            "end": r.end,
            "label": r.label,
            **({"repeater": True} if r.repeater else {}),
            "bands": [
                {
                    "name": b.name,
                    "start": b.start,
                    "end": b.end,
                    "default": b.default,
                    **({"bsrCode": b.bsr_code} if b.bsr_code is not None else {}),
                }
                for b in r.bands
            ],
        }
        for r in resolve_radio_profile(model=model).freq_ranges
    ]


print(json.dumps({model: ranges(model) for model in ("IC-705", "IC-7610")}, separators=(",", ":")))
