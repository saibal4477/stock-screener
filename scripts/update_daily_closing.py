import sys
import os
import argparse

# Add repo root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.daily_updater import update_daily_closing_data

def main():
    parser = argparse.ArgumentParser(description="Update local SQLite DB and GCS bucket with today's stock closing data")
    parser.add_argument("--universe", default="scope_all", help="Universe ID to update (scope_all, sp500, dow30, nasdaq100)")
    parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD (defaults to today)")
    parser.add_argument("--no-gcs", action="store_true", help="Skip uploading to Google Cloud Storage")

    args = parser.parse_args()

    print(f"=== Starting Daily Market Close Updater ===")
    print(f"Universe: {args.universe}")
    print(f"Target Date: {args.date or 'Today (America/New_York)'}")
    
    result = update_daily_closing_data(
        universe_id=args.universe,
        target_date=args.date,
        upload_gcs=not args.no_gcs
    )

    print("\n=== Result ===")
    for k, v in result.items():
        print(f"  {k}: {v}")

    if result.get("status") == "success":
        print("\n✅ Daily closing data successfully updated!")
    else:
        print(f"\n❌ Update failed: {result.get('message')}")
        sys.exit(1)

if __name__ == "__main__":
    main()
