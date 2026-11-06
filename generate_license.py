"""CLI utility to issue a voluntary Arctos license key.

Payment is collected manually. Run this on the server after the payment
is received, then send the printed key to the organizer. The key is
shown once; the database stores only a password hash of its secret half.

Usage:
    python generate_license.py --kind event --teams 8
    python generate_license.py --kind league --teams 6

Events are $5 per team. Leagues are $10 per team.
"""

import argparse
import sys

from app.error_values import Err, Ok
from app.services.license_service import LicenseService, format_dollars


def main() -> None:
    """Parse arguments, store a hashed license, and print the key once."""
    parser = argparse.ArgumentParser(description="Issue a license key and print it once.")
    parser.add_argument(
        "--kind", choices=["event", "league"], required=True, help="event ($5/team) or league ($10/team)"
    )
    parser.add_argument("--teams", type=int, required=True, help="How many teams this payment covers")
    args = parser.parse_args()

    from app import create_app

    app = create_app()
    with app.app_context():
        match LicenseService.issue(args.kind, args.teams):
            case Ok(key):
                cents = LicenseService.price_cents(args.kind) * args.teams
                print(f"{args.kind} license for {args.teams} teams ({format_dollars(cents)})")
                print(key)
            case Err(error):
                print(f"Error: {error}", file=sys.stderr)
                sys.exit(1)


if __name__ == "__main__":
    main()
