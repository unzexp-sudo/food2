"""Outbound WeCom notifications (docs/WECOM_CONTRACTS.md §7).

`service.notify()` is the single dispatcher; `wecom_notify` registers the event
handlers. Importing this package does NOT register handlers — main.py imports
`app.services.notify.wecom_notify` explicitly, last.
"""
