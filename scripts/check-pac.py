#!/usr/bin/env python3
"""Count arm64 PACIASP (pointer-authentication) instructions in native code.

Agent Substrate snapshots a running actor and restores it into a new process.
On arm64 CPUs with pointer authentication, return addresses signed before the
snapshot fail to verify after the restore ("Illegal instruction"), so the
agent image must carry no PAC-signed code. See Dockerfile.agents.

  check-pac.py '/usr/lib/*.so*' ...     # prints per-file counts, exits 1 if any
"""
import sys, glob, os
pat = bytes.fromhex("3f2303d5")   # PACIASP
total = 0
for pattern in sys.argv[1:]:
    for f in sorted(glob.glob(pattern, recursive=True)):
        if os.path.islink(f) and not os.path.exists(f): continue
        d = open(f, "rb").read()
        # count only 4-byte aligned hits (instructions)
        n = sum(1 for i in range(0, len(d) - 3, 4) if d[i:i+4] == pat)
        total += n
        if n: print(f"{n:7d}  {f}")
print(f"PACIASP total: {total}")
sys.exit(1 if total else 0)
