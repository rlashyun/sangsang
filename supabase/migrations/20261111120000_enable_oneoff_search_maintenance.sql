-- PRD 4.5 — explicitly approved temporary maintenance runner.
-- Remove after the one-off derived-table cluster completes; no recurring job is created here.
create extension if not exists pg_cron with schema pg_catalog;
