"""Transport adapters (ADR section 7): framing and session rules only.

``yaesu_cat`` is the first: the FTX-1 CAT fake mounted behind the shared
``rigplane_testkit.serial.endpoints`` TCP/PTY endpoints. An adapter never
decodes protocol semantics beyond wire framing; that lives in
``rigplane_testkit/sim/model/``.
"""
