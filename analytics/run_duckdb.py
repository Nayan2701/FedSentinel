import duckdb

con = duckdb.connect("/tmp/fedsentinel.duckdb")

with open("/analytics/build_gold.sql", "r", encoding="utf-8") as f:
    sql = f.read()

# Print the input source line (read_text/read_parquet/etc.) for sanity
source_lines = [ln.strip() for ln in sql.splitlines() if "read_" in ln.lower()]
print("USING build_gold.sql source:", source_lines[0] if source_lines else "(no read_* found)")

con.execute(sql)

print("\nBuilt gold.edge_security_insights. Sample rows:")
print(
    con.execute(
        """
        select
          node_id, region, pii_leak_risk, top_ip_class, events, quality_score
        from gold.edge_security_insights
        limit 5
        """
    ).fetchdf()
)

print("\nRunning queries.sql:\n")
queries = open("/analytics/queries.sql", "r", encoding="utf-8").read()

# Execute statements one-by-one
for stmt in [s.strip() for s in queries.split(";") if s.strip()]:
    print("===", stmt.splitlines()[0][:80], "...")
    res = con.execute(stmt).fetchdf()
    print(res)
    print()
