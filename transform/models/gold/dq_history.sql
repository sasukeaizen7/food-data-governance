-- Daily history of every rule's failure rate: one row per (run_date, rule). Built incrementally,
-- so each daily run appends today's figures and keeps the past ones.
{{ config(materialized='incremental', unique_key=['run_date', 'rule_id'], incremental_strategy='delete+insert') }}

select
    current_date as run_date,
    rule_id,
    rule_name,
    severity,
    n_checked,
    n_failed,
    pct_failed
from {{ ref('dq_rule_summary') }}
