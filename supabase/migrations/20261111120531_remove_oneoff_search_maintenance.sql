-- PRD 4.5 — complete explicitly approved one-off cleanup; preserve actual search tables.
do $$ begin if exists(select 1 from cron.job) then raise exception 'remaining cron jobs; do not remove extension'; end if; end $$;
drop extension pg_cron;
