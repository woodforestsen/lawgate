import sqlite3
DB = r"D:\桌面\lawgate\data\kb\legal_facts.db"
con = sqlite3.connect(DB)
cur = con.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print("TABLES:", tables)
for t in tables:
    cur.execute(f"PRAGMA table_info({t})")
    cols = [r[1] for r in cur.fetchall()]
    cur.execute(f"SELECT count(*) FROM {t}")
    n = cur.fetchone()[0]
    print(f"\n== {t} == rows={n}")
    print("  cols:", cols)
    for c in ['source_db','validity_status','data_source','is_synthetic',
              'synthetic','provenance','ingest_provenance','law_name',
              'verified','status','article_no']:
        if c in cols:
            cur.execute(f"SELECT {c}, count(*) FROM {t} GROUP BY {c} ORDER BY 2 DESC")
            print(f"  [{c}] dist:", cur.fetchall())
con.close()
