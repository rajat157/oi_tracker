"""Migrate .kite_token file to database, then delete the file."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import init_db, set_setting

TOKEN_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.kite_token')

init_db()

if os.path.exists(TOKEN_FILE):
    with open(TOKEN_FILE, 'r') as f:
        lines = f.read().strip().split('\n')
    
    if len(lines) >= 2:
        token = lines[0]
        date = lines[1][:10]
        set_setting('kite_access_token', token)
        set_setting('kite_token_date', date)
        print(f"Migrated token ({token[:10]}...) dated {date} to database")
    
    os.remove(TOKEN_FILE)
    print(f"Deleted {TOKEN_FILE}")
else:
    print("No .kite_token file found")
