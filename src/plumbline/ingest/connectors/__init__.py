"""Connectors pull from sources that are not file drops and land what they pull as files.

Landing as files means every guarantee of the file loader (ledger, dedup, quarantine, deletion)
applies to API data too, and a landed page can be re-ingested or inspected after the fact.
"""
