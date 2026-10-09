"""The STATE-Bench customer-support environment, copied unchanged.

Source: https://github.com/microsoft/STATE-Bench at 5644b1838d96bc4483da29642d058ecaa6f80f7f, MIT licensed
(``LICENSE`` in this directory). ``environment.py`` implements the 11 tools the
trajectories call; ``policies.py`` holds the return, refund, exchange, cancellation
and warranty rules; ``tools.py`` the tool schemas. Only import lines differ from
the source. ``src.statebench.world`` runs it against records kept in the memory
graph.
"""
