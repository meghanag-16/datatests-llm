"""Sanity checks on the Olist models (plain SQL checks, not dbt tests).

Run from the data_eng folder after building the models:
    uv run python scripts/check_olist.py               # data/olist.duckdb
    uv run python scripts/check_olist.py --snapshot    # data/snapshots/olist.clean.duckdb
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run as a script from data_eng
from common import datasets  # noqa: E402

DS = datasets.get("olist")
KEYS = {"dim_customers": "customer_id", "dim_products": "product_id", "dim_sellers": "seller_id",
        "fct_orders": "order_id", "fct_order_items": "order_item_key"}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--snapshot", action="store_true", help="check the clean snapshot instead")
    args = p.parse_args()
    db = DS.snapshot if args.snapshot else DS.warehouse
    if not db.exists():
        print(f"Missing {db}. Run: uv run python scripts/build_dataset.py --dataset olist")
        return 1
    con = duckdb.connect(str(db), read_only=True)

    def q(sql: str):
        return con.execute(sql).fetchone()[0]

    try:
        print(f"Row counts in {db.relative_to(datasets.ROOT)}:")
        for t in DS.models:
            print(f"  {t:18s} {q(f'select count(*) from {t}'):7d}")
        checks = [(f"{k} unique and not null in {t}",
                   q(f"select count(distinct {k}) = count(*) and count({k}) = count(*) from {t}"))
                  for t, k in KEYS.items()]
        checks += [
            ("every order's customer exists",
             q("select count(*) = 0 from fct_orders where customer_id not in (select customer_id from dim_customers)")),
            ("every item's order exists",
             q("select count(*) = 0 from fct_order_items where order_id not in (select order_id from fct_orders)")),
            ("every item's product exists",
             q("select count(*) = 0 from fct_order_items where product_id not in (select product_id from dim_products)")),
            ("every item's seller exists",
             q("select count(*) = 0 from fct_order_items where seller_id not in (select seller_id from dim_sellers)")),
            ("item_count matches fct_order_items", q("""
                select count(*) = 0 from fct_orders o
                where o.item_count <> (select count(*) from fct_order_items i where i.order_id = o.order_id)""")),
            ("one customer per order", q("select count(*) = count(distinct customer_id) from fct_orders")),
        ]
        # Facts about the real data that a test generator may or may not notice; reported, not checked.
        notes = {
            "orders with no items": q("select count(*) from fct_orders where item_count = 0"),
            "delivered orders without a delivery date": q(
                "select count(*) from fct_orders where order_status = 'delivered' and delivered_customer_at is null"),
            "orders whose payments differ from items + freight by more than 1": q(
                "select count(*) from fct_orders where item_count > 0 "
                "and abs(payment_value - (items_value + freight_value)) > 1"),
            "products without a category": q("select count(*) from dim_products where category is null"),
        }
    finally:
        con.close()

    failed = 0
    for name, ok in checks:
        print(f"  {'OK  ' if ok else 'FAIL'} {name}")
        failed += not ok
    print("Observations (not failures):")
    for name, n in notes.items():
        print(f"  {name}: {n}")
    print("All checks passed." if not failed else f"{failed} checks failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
