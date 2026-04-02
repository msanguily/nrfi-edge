"""Apply SQL migration files to Supabase via direct Postgres connection."""
import os
import sys
import glob
import psycopg2
from dotenv import load_dotenv

load_dotenv()

CONNECTION_STRING = os.environ["SUPABASE_CONNECTION_STRING"]
MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "supabase", "migrations")


def main():
    conn = psycopg2.connect(CONNECTION_STRING, sslmode="require")
    conn.autocommit = True
    cur = conn.cursor()

    files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.sql")))
    if not files:
        print("No migration files found.")
        sys.exit(1)

    for f in files:
        name = os.path.basename(f)
        with open(f) as fh:
            sql = fh.read()
        print(f"Applying {name}... ", end="", flush=True)
        try:
            cur.execute(sql)
            print("OK")
        except Exception as e:
            print(f"FAILED: {e}")
            sys.exit(1)

    # Verify
    cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
        ORDER BY table_name;
    """)
    rows = cur.fetchall()
    print(f"\nCreated {len(rows)} tables in public schema:")
    for row in rows:
        print(f"  - {row[0]}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
