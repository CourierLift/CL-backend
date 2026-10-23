-- REVIEW ONLY. Execute only during the specifically approved credential setup.
-- Never place a password in this file, source control or a command-line argument.
-- Assign a separately generated password through the approved secret channel.
-- Do not reuse the application/database-owner login for scheduled backups.
CREATE ROLE courierlifts_backup LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
GRANT CONNECT ON DATABASE courierlifts_db TO courierlifts_backup;
GRANT USAGE ON SCHEMA public TO courierlifts_backup;
GRANT SELECT ON public.alembic_version, public.users, public.courier_profiles,
    public.quotes, public.orders, public.delivery_proofs, public.reward_events
    TO courierlifts_backup;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO courierlifts_backup;
ALTER ROLE courierlifts_backup SET default_transaction_read_only = on;
-- Future schema revisions require deliberate grants/review; no blanket
-- pg_read_all_data membership, default privileges or write capability is added.
