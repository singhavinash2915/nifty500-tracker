-- Bytes each job read from the API.
--
-- The free plan's egress allowance is metered per organization, and running
-- over it restricted every project in the organization, including one that
-- had nothing to do with this tracker. Nothing warned beforehand: the pipeline
-- read about 325MB a night for weeks and the first signal was a 402.
--
-- Each job now records what it pulled, and `verify` sums the calendar month
-- and fails the nightly run once the total passes its budget, so the next
-- overrun arrives as a failure notification rather than a restriction.
alter table n500.ingestion_runs
  add column if not exists bytes_read bigint;
