"""camsim: ISO / exposure-time camera simulator.

From one scene raw capture (ISO 50, 100 ms) synthesise the frame the phone would produce at any
ISO in [50, 1600] and exposure in [1 ms, 100 ms], and validate against a real frame sweep.
"""

__version__ = "0.1.0"

from .params import ISO_RANGE, EXPOSURE_RANGE_S, ev  # noqa: F401
