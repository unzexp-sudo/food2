"""Master data service layer (master data agent).

Business logic for customers, catalog, wholesalers, and contracts.
Services never commit on their own — the router owns the transaction
boundary, then calls ``db.commit()``.
"""
