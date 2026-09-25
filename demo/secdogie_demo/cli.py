"""`secdogie-demo` -- run the end-to-end vertical slice and print the story."""
from __future__ import annotations

import sys

from .vertical_slice import run_slice


def main(argv: list[str] | None = None) -> int:
    result = run_slice(narrate=True)
    print("\n--- results ---")
    for name, ok, detail in result.steps:
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name}" + (f"  ({detail})" if detail else ""))
    print(f"\nreal structural agent loop exercised: {result.used_real_loop}")
    print("OVERALL:", "PASS" if result.ok else "FAIL")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
