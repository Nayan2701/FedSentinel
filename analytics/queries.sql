-- 1) Insights by region
SELECT region, count(*) AS insights, round(avg(quality_score), 3) AS avg_quality
FROM gold.edge_security_insights
GROUP BY 1
ORDER BY insights DESC;

-- 2) Risk distribution
SELECT pii_leak_risk, count(*) AS insights
FROM gold.edge_security_insights
GROUP BY 1
ORDER BY insights DESC;

-- 3) LLM vs fallback
SELECT summary_source, count(*) AS insights
FROM gold.edge_security_insights
GROUP BY 1
ORDER BY insights DESC;

-- 4) Top actions by region (top_actions is a LIST)
WITH exploded AS (
  SELECT region, unnest(top_actions) AS action
  FROM gold.edge_security_insights
)
SELECT region, action, count(*) AS occurrences
FROM exploded
GROUP BY 1,2
ORDER BY occurrences DESC
LIMIT 20;
