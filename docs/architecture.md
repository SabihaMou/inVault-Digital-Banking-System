# Architecture and ledger model

FinVault uses a standard-library ThreadingHTTPServer and SQLite. All monetary values are integer paisa (BDT minor units). Transfers acquire an application lock and SQLite `BEGIN IMMEDIATE` transaction, then validate source/destination, check funds, create one transaction and two opposing entries, and store an idempotency key before commit. The transaction's entries always sum to zero. For the seeded fictional opening balances, a dedicated frozen treasury account is the offsetting counterparty.

SQLite's transaction serialization supports this local single-process demo. For multiple workers or deployment, migrate to PostgreSQL and row-level locking, replace in-memory sessions, and adopt a maintained production web framework.
