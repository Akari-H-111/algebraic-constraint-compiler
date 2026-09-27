"""CLI: compile typed JSON or independently verify an exported compilation run."""

import argparse
import sys

from .compiler import compile_reconstruction
from .ir import InputError, MAX_BYTES, canonical, load_json
from .verifier import verify_run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("compile", "verify"))
    parser.add_argument("input", help="JSON file, or - for stdin")
    args = parser.parse_args()
    try:
        if args.input == "-":
            content = sys.stdin.buffer.read(MAX_BYTES + 1)
        else:
            with open(args.input, "rb") as stream:
                content = stream.read(MAX_BYTES + 1)
        payload = load_json(content)
        result = compile_reconstruction(payload) if args.action == "compile" else verify_run(payload)
    except (InputError, OSError) as exc:
        result = {"status": "INFRASTRUCTURE_ERROR" if isinstance(exc, OSError) else "ASSUMPTION_REQUIRED",
                  "certificate_verified": False, "code": getattr(exc, "code", "IO_ERROR"), "message": str(exc)}
    sys.stdout.buffer.write(canonical(result) + b"\n")
    # A certified inconsistency is a successful computation, not a CLI failure.
    return 0 if result["certificate_verified"] and result["status"] in ("VERIFIED", "MATHEMATICALLY_REJECTED") else 1


if __name__ == "__main__":
    sys.exit(main())
