"""Data repositories backing the memory tiers (Phase 4) and governance /
lineage / approval storage (Phase 5).

Each repository is a protocol (interface) plus an in-memory reference
implementation suitable only for local development and tests. Production
must configure a durable backend (Cosmos DB / Azure SQL, and Azure AI
Search for enterprise knowledge) per the Architecture Principles in
``.github/copilot-instructions.md``; that integration is reserved for a
later phase and does not require changes to the memory or governance
service layers.
"""
