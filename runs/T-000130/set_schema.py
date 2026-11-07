# Set a throwaway manifest's RECORDED schema revision, so the read path's
# rebuild refusal (exit 4, empty stdout) can be measured live.
# Usage: python set_schema.py <manifest.db> <revision>   (7 restores it)
import sqlite3
import sys

db, value = sys.argv[1], sys.argv[2]
conn = sqlite3.connect(db)
conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (value,))
conn.commit()
row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
conn.close()
print(f"schema_version now {row[0]}")
