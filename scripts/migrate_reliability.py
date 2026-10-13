"""Run on a backed-up/offline copy; no network or embedding requests."""
import argparse
from govtech_scraper.db import Database
from govtech_scraper.migration import migrate

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default='govtech.db')
    args = parser.parse_args()
    db = Database(args.db)
    try:
        migrate(db)
        print('Offline reliability migration complete')
    finally:
        db.close()
