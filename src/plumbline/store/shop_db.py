"""A small shop's own Postgres database, and a day of the changes a real application makes to it.

The change-capture connector (ingest/connectors/postgres_cdc.py) is tested against it on a real Postgres:
sign-ups, an email changed, a plan upgraded twice in one transaction, an order paid then shipped, a cancelled
order deleted, a customer who asked to be removed deleted with their orders, and a column added mid-day (the
contract holds it back until a person adds it). Invented people, .test emails.
"""
from __future__ import annotations

SCHEMA = """
DROP TABLE IF EXISTS orders; DROP TABLE IF EXISTS customers;
CREATE TABLE customers (id integer PRIMARY KEY, first_name text, last_name text, email text, zip text,
                        plan text, updated_at timestamptz DEFAULT now());
CREATE TABLE orders (id integer PRIMARY KEY, customer_id integer REFERENCES customers(id) ON DELETE CASCADE,
                     total numeric(10,2), status text, created_at timestamptz DEFAULT now());
"""
FIRST = [(1, "Ann", "Lee", "ann.lee@mail.test", "11201", "free"), (2, "Bo", "Kim", "bo.kim@mail.test", "10001", "pro"),
         (3, "Cy", "Diaz", "cy.diaz@mail.test", "11215", "free"), (4, "Dee", "O'Neil", "dee@mail.test", "10463", "team")]

# one entry per transaction, in the order the application commits them
DAY = [
    ["INSERT INTO customers (id, first_name, last_name, email, zip, plan) VALUES (5, 'Eve', 'Park', 'eve.park@mail.test', '11211', 'free')",
     "INSERT INTO orders (id, customer_id, total, status) VALUES (103, 5, 42.50, 'placed')"],
    ["UPDATE customers SET email = 'ann@newmail.test', updated_at = now() WHERE id = 1"],
    ["UPDATE customers SET plan = 'pro' WHERE id = 3", "UPDATE customers SET plan = 'team' WHERE id = 3"],
    ["UPDATE orders SET status = 'paid' WHERE id = 101"],
    ["UPDATE orders SET status = 'shipped' WHERE id = 101"],
    ["DELETE FROM orders WHERE id = 102"],
    ["ALTER TABLE customers ADD COLUMN referral_code text",
     "INSERT INTO customers (id, first_name, last_name, email, zip, plan, referral_code) VALUES (6, 'Fay', 'Ng', 'fay@mail.test', '10025', 'pro', 'ANN-1')"],
    ["DELETE FROM customers WHERE id = 2"],                       # cascades to Bo's order 100
]
FIRST_ORDERS = [(100, 2, 19.99, "paid"), (101, 1, 120.00, "placed"), (102, 3, 15.00, "placed")]

# what the current view must show at the end of the day
END_CUSTOMERS = {1: "ann@newmail.test", 3: "cy.diaz@mail.test", 4: "dee@mail.test", 5: "eve.park@mail.test", 6: "fay@mail.test"}
END_PLANS = {1: "free", 3: "team", 4: "team", 5: "free", 6: "pro"}
END_ORDERS = {101: "shipped", 103: "placed"}


def setup(con) -> None:
    con.autocommit = True
    cur = con.cursor()
    cur.execute(SCHEMA)
    cur.executemany("INSERT INTO customers (id, first_name, last_name, email, zip, plan) VALUES (%s,%s,%s,%s,%s,%s)", FIRST)
    cur.executemany("INSERT INTO orders (id, customer_id, total, status) VALUES (%s,%s,%s,%s)", FIRST_ORDERS)


def play(con, steps=None) -> int:
    """Commit the day's transactions (or the given slice of them); returns how many."""
    con.autocommit = False
    cur = con.cursor()
    steps = DAY if steps is None else steps
    for tx in steps:
        for sql in tx:
            cur.execute(sql)
        con.commit()
    con.autocommit = True
    return len(steps)
