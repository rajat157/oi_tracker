"""Explore the OI tracker database schema and data availability."""
import sqlite3
import os

db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'oi_tracker.db')
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Get all tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cursor.fetchall()]
print("=== TABLES ===")
for t in tables:
    cursor.execute(f"SELECT COUNT(*) FROM {t}")
    count = cursor.fetchone()[0]
    print(f"  {t}: {count} rows")

# Get schema for key tables
print("\n=== SCHEMAS ===")
for t in tables:
    cursor.execute(f"PRAGMA table_info({t})")
    cols = cursor.fetchall()
    print(f"\n{t}:")
    for col in cols:
        print(f"  {col[1]} ({col[2]})")

# Check OI analysis data range
print("\n=== DATA RANGE ===")
for t in ['oi_analysis', 'option_prices', 'trades']:
    if t in tables:
        try:
            cursor.execute(f"SELECT MIN(timestamp), MAX(timestamp) FROM {t}")
            r = cursor.fetchone()
            print(f"{t}: {r[0]} to {r[1]}")
        except:
            pass

# Sample trades
if 'trades' in tables:
    print("\n=== SAMPLE TRADES ===")
    cursor.execute("SELECT * FROM trades LIMIT 5")
    cols = [d[0] for d in cursor.description]
    print("Columns:", cols)
    for row in cursor.fetchall():
        print(row)

# Check option_prices for selling analysis
if 'option_prices' in tables:
    print("\n=== OPTION PRICES SAMPLE ===")
    cursor.execute("SELECT * FROM option_prices LIMIT 5")
    cols = [d[0] for d in cursor.description]
    print("Columns:", cols)
    for row in cursor.fetchall():
        print(row)

# Check what data we have per day
if 'oi_analysis' in tables:
    print("\n=== ANALYSIS PER DAY ===")
    cursor.execute("""
        SELECT DATE(timestamp) as day, COUNT(*) as records, 
               MIN(timestamp), MAX(timestamp)
        FROM oi_analysis 
        GROUP BY DATE(timestamp) 
        ORDER BY day
    """)
    for row in cursor.fetchall():
        print(f"  {row[0]}: {row[1]} records ({row[2]} to {row[3]})")

conn.close()
