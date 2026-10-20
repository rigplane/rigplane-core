"""Standalone radio simulator processes for downstream dogfooding (MOR-3593).

``python -m rigplane_testkit.sim serve --radio ic7610`` runs the IC-7610 LAN
simulator as its own process with a small local HTTP control endpoint, so an
installed app — for example the Pro dogfood scorecard — can be pointed at a
simulated radio without a test harness in between. The launcher and control
endpoint live in :mod:`rigplane_testkit.sim.serve`; the interface contract is
``docs/internals/sim-serve-contract.md``.
"""
