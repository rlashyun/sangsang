-- Only after explicit approval to temporarily enable pg_cron.
-- One dated run, not an every-minute recurring task. Monitor and unschedule after completion.
select cron.schedule('seonghyun-search-cluster-once',
 to_char((clock_timestamp()+interval '1 minute') at time zone 'GMT','MI HH24 DD MM')||' *',
 $job$set statement_timeout='300s'; set lock_timeout='5s';
 set enable_indexscan=off; set maintenance_work_mem='64MB';
 cluster public.found_item_search_vectors using found_item_search_vectors_location;
 analyze public.found_item_search_vectors;
 cluster public.found_item_search_documents using found_item_search_documents_location;
 analyze public.found_item_search_documents;$job$);

-- After observing success or failure:
-- select cron.unschedule(jobid) from cron.job where jobname='seonghyun-search-cluster-once';
-- Confirm no remaining jobs before removing the newly enabled extension via a forward migration.
