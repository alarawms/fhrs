"""Pure, heavily-tested domain logic. No settings access, no DB/network I/O.

Loaders here read local data files from explicit paths handed in by the caller —
never from configuration — so every function is deterministic given its inputs.
"""
