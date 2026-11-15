-- PRD 4.5 — physical locality for exact filtered scans; original tables are untouched.
-- Execute after bootstrap. New writes stay correct through the existing triggers.
set lock_timeout='2s';
set statement_timeout='120s';
set enable_indexscan=off;
set maintenance_work_mem='64MB';
cluster public.found_item_search_vectors using found_item_search_vectors_location;
analyze public.found_item_search_vectors;
reset maintenance_work_mem;
reset enable_indexscan;
reset statement_timeout;
reset lock_timeout;
