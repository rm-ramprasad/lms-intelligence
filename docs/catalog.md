# KPI catalog

_Generated from `semantic/glossary.yaml`, `value_aliases.yaml` and `dimension_values.yaml` by `scripts/build_catalog_doc.py`. Do not edit by hand._

## KPIs

| KPI | Definition | Formula | Unit | Behavior | Synonyms | Owner | Status |
|---|---|---|---|---|---|---|---|
| **Total outstanding** (`total_outstanding`) | Sum of TotalOS across accounts at a month-end snapshot. Also called loan book, portfolio, AUM or POS. | Σ TotalOS | ₹ Cr | Month-end balance. Compare months; never add across months. | outstanding, total outstanding, total os, os, loan book, book size, book, portfolio, aum, pos, principal outstanding, portfolio outstanding | Finance | approved |
| **Number of accounts** (`account_count`) | Distinct loan accounts present in a month-end snapshot, active or closed. | COUNT DISTINCT account | count | Month-end count. Compare months; never add across months. | accounts, number of accounts, account count, no of accounts, loans, number of loans, loan count, total accounts | Operations | approved |
| **Number of active accounts** (`active_account_count`) | Distinct accounts with AcStatus = Active in a month-end snapshot. | COUNT DISTINCT account WHERE status = Active | count | Month-end count. Compare months; never add across months. | active accounts, active loans, live accounts, live loans, number of active accounts, active account count | Operations | approved |
| **Sanctioned amount** (`sanctioned_amount`) | Sum of SanctionedAmount for accounts present in a month-end snapshot. | Σ SanctionedAmount | ₹ Cr | Month-end position. Compare months; never add across months. | sanctioned amount, sanction amount, sanctioned, sanctions, sanctioned value | Credit | approved |
| **Disbursement** (`disbursement_amount`) | Sum of DisbursementAmount over the month-end rows in the period. | Σ DisbursementAmount over months in period | ₹ Cr | Monthly flow. Adds up across months; needs a period. | disbursement, disbursements, disbursed, disbursal, disbursals, disbursement amount, amount disbursed | Business | approved |
| **Demand** (`demand_amount`) | Sum of DemandAmount over the months in the period. | Σ DemandAmount over months in period | ₹ Cr | Monthly flow. Adds up across months; needs a period. | demand, demand amount, emi demand, dues raised, billing, billed | Collections | approved |
| **Repayment** (`repayment_amount`) | Sum of RepaymentAmount over the months in the period; negative reversals are netted. | Σ RepaymentAmount over months in period | ₹ Cr | Monthly flow. Adds up across months; needs a period. | repayment, repayments, repaid, repayment amount, collections, collection amount, total collection, total collections, amount collected | Collections | approved |
| **Collection against current-month demand** (`current_demand_collected`) | Sum of RepaymentForCurrentMonthDemand over the months in the period. | Σ RepaymentForCurrentMonthDemand | ₹ Cr | Monthly flow. Adds up across months; needs a period. | current demand collected, collection against demand, current month collection, current collection, collection against current demand | Collections | approved |
| **Collection efficiency** (`collection_efficiency`) | Collection against current-month demand divided by demand, over the period. | Current-month collection ÷ Demand | % | Ratio over a period. Computed from totals, never averaged. | collection efficiency, ce, collection %, collection percentage, collection ratio, efficiency, collection rate | Collections | approved |
| **PAR 30+ outstanding** (`par_30_plus_amount`) | Sum of PAR 31-60, PAR 61-90 and PAR 90+ outstanding at a month-end snapshot. | PAR 31-60 + PAR 61-90 + PAR 90+ | ₹ Cr | Month-end balance. Compare months; never add across months. | par 30 amount, par30 amount, par 30 outstanding, par30 outstanding, par 30+ amount, par 30 os, portfolio at risk amount | Credit risk | approved |
| **PAR 30+ ratio** (`par_30_plus_ratio`) | PAR 30+ outstanding divided by total outstanding at a month-end snapshot. | PAR 30+ outstanding ÷ Total outstanding | % | Month-end ratio. Computed from totals, never averaged. | par 30 ratio, par30 ratio, par 30 %, par30 %, par 30+ ratio, par 30 percentage, par 30+ %, par30+ %, portfolio at risk, par ratio | Credit risk | approved |
| **NPA outstanding** (`npa_outstanding`) | Total outstanding of accounts with NPAFlag = Yes at a month-end snapshot. | Σ TotalOS where NPA | ₹ Cr | Month-end balance. Compare months; never add across months. | npa outstanding, npa amount, npa os, gross npa amount, npa book, npa value | Credit risk | approved |
| **Gross NPA ratio** (`gross_npa_ratio`) | NPA outstanding divided by total outstanding at a month-end snapshot. | NPA outstanding ÷ Total outstanding | % | Month-end ratio. Computed from totals, never averaged. | gnpa, gross npa, gross npa ratio, npa ratio, npa %, npa percentage, gnpa %, gnpa ratio, gross npa % | Credit risk | approved |
| **Written-off outstanding** (`write_off_outstanding`) | Pending approval. Whether written-off accounts should report TotalOS is unconfirmed. | Σ TotalOS where written off | ₹ Cr | Not yet available. | write off amount, write-off amount, written off amount, written-off amount, write off outstanding, write-off outstanding | Finance | pending_approval |

## Breakdowns and filters

| Dimension | Synonyms | Values |
|---|---|---|
| Month (`month`) | month, months, month end, month-end, monthly, month-wise, monthwise, trend, over time, month on month, mom | (time grain) |
| Calendar quarter (`quarter`) | quarter, quarters, quarter end, quarter-end, quarterly, qtr, quarter-wise, quarterwise, qoq | (time grain) |
| Calendar year (`year`) | calendar year | (time grain) |
| Fiscal year (Apr-Mar, named by end year) (`fiscal_year`) | year, years, yearly, annual, annually, fiscal year, financial year, fy, year-wise, yoy | (time grain) |
| Loan group (`loan_group`) | loan group, loan groups, group, groups, loan type, loan types, product line, product lines | Enterprise Flex, Growth Finance, Rural Enterprise, Working Capital Plus |
| Product (`product_code`) | product, products, product code, product-wise, productwise | DF-EF, DF-GF, DF-RE, DF-WC |
| Branch (`branch_code`) | branch, branches, branch-wise, branchwise, office, offices | DEMO-EAST, DEMO-NORTH, DEMO-SOUTH, DEMO-WEST |
| State (`state_name`) | state, states, state-wise, statewise, region, regions, geography, location, locations | Delhi, Gujarat, Karnataka, Madhya Pradesh, Maharashtra, Rajasthan, Tamil Nadu, Telangana, Uttar Pradesh, West Bengal |
| State code (`state_code`) |  | DL, GJ, KA, MH, MP, RJ, TG, TN, UP, WB |
| Account status (`account_status`) | status, account status, a/c status | Active, Closed |
| New business / renewal (`business_nature`) | nature, business type, new vs renewal, new or renewal, nature of business | New Business, Renewal |
| PAR bucket (`par_bucket`) | par bucket, par buckets, dpd bucket, dpd buckets, bucket, buckets, overdue bucket, ageing, aging, dpd | 1-30 DPD, 31-60 DPD, 61-90 DPD, 90+ DPD, Current |
| Ticket size (`ticket_size`) | ticket size, ticket sizes, ticket, size, loan size, ticket bucket | 1.5 - 2.5 Cr, Above 2.5 Cr, Up to 1.5 Cr |
| NPA (`npa_flag`) | npa status, npa flag, npa vs standard, npa classification | Yes, No |
| Written off (`write_off_flag`) | write-off status, write off status, written off flag | Yes, No |
| Restructured (`restructure_flag`) | restructured status, restructure flag, restructuring status | Yes, No |

## Terms

- **DPD**: Days past due on the oldest unpaid instalment.
- **PAR**: Portfolio at risk: outstanding of accounts overdue beyond a threshold (e.g. 30 days).
- **NPA**: Non-performing asset: account flagged NPA by the lender (NPAFlag = Yes).
- **Month-end snapshot**: Balances are captured once per month at month-end; balances are not added across months.
- **Fiscal year**: 1 April to 31 March, named by end year (FY 2026 = Apr 2025 to Mar 2026).
- **Fiscal quarter**: Q1 = Apr-Jun, Q2 = Jul-Sep, Q3 = Oct-Dec, Q4 = Jan-Mar (e.g. Q4 FY26 = Jan-Mar 2026).
- **Ticket size**: Bucket of loan size (Up to 1.5 Cr, 1.5 - 2.5 Cr, Above 2.5 Cr).

## Example questions

- What is total outstanding by state as of the latest snapshot? _(also: Outstanding by state; state-wise loan book latest; What is the portfolio by region as of now?)_
- How many active accounts are there in each loan group as of March 2026? _(also: Active accounts by loan group for March 2026; live loans per loan group Mar-26; How many active loans in each group in Mar 2026?)_
- What is the monthly disbursement trend for FY 2026? _(also: Monthly disbursement FY26; disbursements by month for FY 2025-26; Show disbursal trend month-wise in FY 2026)_
- What is the PAR 30+ ratio by branch as of the latest snapshot? _(also: PAR 30 ratio by branch; branch-wise portfolio at risk latest; What is the PAR 30+ % for each branch?)_
- What is the gross NPA ratio by product as of the latest snapshot? _(also: GNPA by product; gross NPA % product-wise latest; What is the NPA ratio for each product?)_
- What was total outstanding at the end of each quarter of FY 2026? _(also: Quarterly outstanding FY26; AUM by quarter for FY 2025-26; loan book at each quarter end in FY 2026)_
- What was the monthly collection efficiency in FY 2026? _(also: Collection efficiency by month FY26; monthly CE for FY 2025-26; Show collection efficiency trend month-wise in FY 2026)_
