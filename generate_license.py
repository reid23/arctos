"""CLI utility for voluntary Arctos license keys.

Payment is collected manually. Run this on the server after the payment
is received, then send the printed key to the organizer. The key is
shown once; the database stores only a password hash of its secret half.
Each license has an integer id. Use that id to list licenses or to change
the team allowance and the amount paid. Those two numbers are recorded
separately; neither is calculated from the other.

Usage:
    python generate_license.py --kind event --teams 8 --paid 40
    python generate_license.py --kind league --teams 6 --paid 60
    python generate_license.py --list
    python generate_license.py --id 3 --teams 12
    python generate_license.py --id 3 --paid 50
    python generate_license.py --id 3 --teams 12 --paid 60

The listed price is $5 per team for events and $10 per team for leagues.
``--paid`` is the amount actually received, in dollars.
"""

import argparse
import sys

from app.error_values import Err, Ok
from app.services.license_service import LicenseService, format_dollars, parse_paid_cents


def _paid_arg(text: str) -> int:
    match parse_paid_cents(text):
        case Ok(cents):
            return cents
        case Err(error):
            raise argparse.ArgumentTypeError(str(error))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Issue, list, and update voluntary Arctos license keys.")
    parser.add_argument("--list", action="store_true", help="Show every license")
    parser.add_argument("--id", type=int, help="License id to update")
    parser.add_argument("--kind", choices=["event", "league"], help="Kind of license to issue")
    parser.add_argument("--teams", type=int, help="Teams a new license covers, or the new allowance")
    parser.add_argument("--paid", type=_paid_arg, help="Amount received, in dollars (for example 40 or 40.50)")
    return parser


def _holder(row) -> str:
    if row.tournament_url:
        return f"event {row.tournament_url}"
    if row.league_url:
        return f"league {row.league_url}"
    return "unused"


def _print_licenses(rows) -> None:
    if not rows:
        print("No licenses.")
        return
    print(f"{'id':<6} {'kind':<8} {'teams':>5} {'paid':>10}  holder")
    for row in rows:
        paid = format_dollars(row.amount_paid_cents)
        print(f"{row.id:<6} {row.kind:<8} {row.team_allowance:>5} {paid:>10}  {_holder(row)}")


def main() -> None:
    """Issue a key, list licenses, or change an allowance or payment."""
    args = _parser().parse_args()
    listing = args.list
    updating = args.id is not None
    if listing and (updating or args.kind is not None or args.teams is not None or args.paid is not None):
        print("Error: --list does not take other options.", file=sys.stderr)
        sys.exit(2)
    if updating and args.kind is not None:
        print("Error: --kind is only for a new license.", file=sys.stderr)
        sys.exit(2)
    if updating and args.teams is None and args.paid is None:
        print("Error: updating a license needs --teams, --paid, or both.", file=sys.stderr)
        sys.exit(2)
    if not listing and not updating and (args.kind is None or args.teams is None or args.paid is None):
        print("Error: issuing a license needs --kind, --teams, and --paid.", file=sys.stderr)
        sys.exit(2)

    from app import create_app

    app = create_app()
    with app.app_context():
        if listing:
            _print_licenses(LicenseService.list_licenses())
            return
        if updating:
            match LicenseService.update_license(args.id, team_count=args.teams, amount_paid_cents=args.paid):
                case Ok(row):
                    print(f"license {row.id}: {row.team_allowance} teams, {format_dollars(row.amount_paid_cents)}")
                case Err(error):
                    print(f"Error: {error}", file=sys.stderr)
                    sys.exit(1)
            return
        match LicenseService.issue(args.kind, args.teams, args.paid):
            case Ok(issued):
                paid = format_dollars(args.paid)
                print(f"{args.kind} license {issued.license_id} for {args.teams} teams ({paid})")
                print(issued.key)
            case Err(error):
                print(f"Error: {error}", file=sys.stderr)
                sys.exit(1)


if __name__ == "__main__":
    main()
