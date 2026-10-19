-- Tier 2 data checks (approved 2026-10-19). Aggregates only; no account identifiers are selected.

-- check: C1 grain (one row per account per month-end)
SELECT
  (SELECT count(*) FROM analytics_poc.v_account_summary) AS n_rows,
  (SELECT count(*) FROM (SELECT DISTINCT account_number, transaction_date FROM analytics_poc.v_account_summary)) AS n_distinct_keys,
  (SELECT count_if(account_number IS NULL) FROM analytics_poc.v_account_summary) AS null_account_numbers;

-- check: C2 PAR 90+ amount vs 90+ bucket outstanding (per month)
SELECT count(*) AS months,
  count_if(abs(par90 - os90) > 1) AS months_mismatched,
  sum(par90) AS sum_par_over_90, sum(os90) AS sum_total_os_in_90_bucket,
  max(abs(par90 - os90)) AS max_abs_diff
FROM (
  SELECT transaction_date, sum(par_over_90) AS par90,
         sum(CASE WHEN par_bucket = '90+ DPD' THEN total_os ELSE 0 END) AS os90
  FROM analytics_poc.v_account_summary GROUP BY transaction_date
);

-- check: C3a bucket transitions month-over-month (all months, and latest month)
WITH t AS (
  SELECT transaction_date AS d, par_bucket AS b,
         lag(par_bucket) OVER (PARTITION BY account_number ORDER BY transaction_date) AS pb,
         lag(transaction_date) OVER (PARTITION BY account_number ORDER BY transaction_date) AS pd
  FROM analytics_poc.v_account_summary
)
SELECT pb AS from_bucket, b AS to_bucket, count(*) AS n_all_months,
       count_if(d = (SELECT max(transaction_date) FROM analytics_poc.v_account_summary)) AS n_latest_month
FROM t
WHERE pd = last_day_of_month(date_add('month', -1, d))
GROUP BY 1, 2
ORDER BY 1, 2;

-- check: C3b entries, exits and flag changes (all months, and latest month)
WITH t AS (
  SELECT transaction_date AS d, ac_status AS s, write_off_flag AS w, npa_flag AS n,
         lag(transaction_date) OVER (PARTITION BY account_number ORDER BY transaction_date) AS pd,
         lead(transaction_date) OVER (PARTITION BY account_number ORDER BY transaction_date) AS nd,
         lag(ac_status) OVER (PARTITION BY account_number ORDER BY transaction_date) AS ps,
         lag(write_off_flag) OVER (PARTITION BY account_number ORDER BY transaction_date) AS pw,
         lag(npa_flag) OVER (PARTITION BY account_number ORDER BY transaction_date) AS pn
  FROM analytics_poc.v_account_summary
), m AS (SELECT max(transaction_date) AS maxd FROM analytics_poc.v_account_summary)
SELECT
  count_if(pd IS NULL) AS new_accounts_all, count_if(pd IS NULL AND d = maxd) AS new_accounts_latest,
  count_if(nd IS NULL AND d < maxd) AS disappeared_all,
  count_if(s = 'Closed' AND ps = 'Active') AS active_to_closed_all,
  count_if(w AND NOT pw) AS new_write_offs_all, count_if(w AND NOT pw AND d = maxd) AS new_write_offs_latest,
  count_if(n AND NOT pn) AS new_npa_all, count_if(n AND NOT pn AND d = maxd) AS new_npa_latest,
  count_if(NOT n AND pn) AS npa_upgrades_all,
  count_if(pd IS NOT NULL AND pd <> last_day_of_month(date_add('month', -1, d))) AS month_gaps
FROM t CROSS JOIN m;
