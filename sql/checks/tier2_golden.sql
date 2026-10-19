-- Tier 2 golden checks: hand-written, independent of bi_investigate templates. Aggregates only.

-- check: G1 PAR 90+ and GNPA ratios at golden month-ends
SELECT transaction_date AS d,
  CAST(sum(par_over_90) AS double) / sum(total_os) AS par90_ratio,
  CAST(sum(CASE WHEN npa_flag THEN total_os ELSE 0 END) AS double) / sum(total_os) AS gnpa_ratio
FROM analytics_poc.v_account_summary
WHERE transaction_date IN (DATE '2024-01-31', DATE '2024-02-29', DATE '2026-02-28', DATE '2026-03-31')
GROUP BY 1 ORDER BY 1;

-- check: G2 collection efficiency at golden months
SELECT transaction_date AS d,
  CAST(sum(repayment_for_current_month_demand) AS double) / sum(demand_amount) AS ce
FROM analytics_poc.v_account_summary
WHERE transaction_date IN (DATE '2025-03-31', DATE '2025-04-30', DATE '2026-02-28', DATE '2026-03-31')
GROUP BY 1 ORDER BY 1;

-- check: G3 PAR 90+ bridge Feb -> Mar 2026 via window functions (opening, closing, roll-in from 61-90, exits)
WITH t AS (
  SELECT transaction_date AS d, par_bucket AS b, COALESCE(par_over_90, 0) AS p, ac_status AS s, write_off_flag AS w,
    lag(par_bucket) OVER (PARTITION BY account_number ORDER BY transaction_date) AS pb,
    lag(transaction_date) OVER (PARTITION BY account_number ORDER BY transaction_date) AS pd,
    lag(ac_status) OVER (PARTITION BY account_number ORDER BY transaction_date) AS ps,
    lag(write_off_flag) OVER (PARTITION BY account_number ORDER BY transaction_date) AS pw,
    lead(transaction_date) OVER (PARTITION BY account_number ORDER BY transaction_date) AS nd
  FROM analytics_poc.v_account_summary
  WHERE transaction_date IN (DATE '2026-02-28', DATE '2026-03-31')
)
SELECT
  sum(CASE WHEN d = DATE '2026-02-28' THEN p ELSE 0 END) AS opening,
  sum(CASE WHEN d = DATE '2026-03-31' THEN p ELSE 0 END) AS closing,
  sum(CASE WHEN d = DATE '2026-03-31' AND pd = DATE '2026-02-28' AND b = '90+ DPD' AND pb = '61-90 DPD'
           AND NOT (w AND NOT pw) AND NOT (s = 'Closed' AND ps <> 'Closed') THEN p ELSE 0 END) AS rolled_in_61_90,
  sum(CASE WHEN d = DATE '2026-02-28' AND nd IS NULL THEN -p ELSE 0 END) AS exited
FROM t;
