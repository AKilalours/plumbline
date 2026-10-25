"""Module B data products: the golden person, address history, life events, households, audiences.

The models are SQL in dbt/ (dbt-duckdb, built into the warehouse's `products` schema). This package
runs them and scores them against the synthetic answer key.
"""
