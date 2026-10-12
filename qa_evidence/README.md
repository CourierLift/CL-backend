# qa/quote-idempotency evidence (base a320a65)
- qa_round2_sqlite.txt / qa_round2_pg.txt: ORIGINAL round-2 output at a320a65 (unchanged).
- original_repros/: ORIGINAL repro tests (unchanged). tests/test_qa_round2.py and
  tests/test_qa_concurrent_create.py are the same repros converted to assert corrected behavior.
- alembic_heads_before_0005.txt: `alembic heads` at a320a65 -> 0004_pricing_snapshot.
- suite_*.txt: full pytest runs, Python 3.11.17. PG = PostgreSQL 16.15 disposable cluster on port 5433.
- alembic_cycle_*.txt: upgrade head / downgrade base / upgrade head / downgrade 0004 / upgrade head (disposable DBs only).
- api_examples_observed.txt: real responses from api_examples_script.py against a throwaway SQLite DB.
Compatibility edits to pre-existing tests: every edited call is marked "COMPAT" in source.
