"""Load the Olist Brazilian E-Commerce CSV files into DuckDB (data/olist.duckdb, schema raw).

Get the data once from Kaggle (a free account is needed):
  https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce  ->  Download
Put the downloaded zip file in data/raw/olist/ (any file name ending in .zip), or extract the
CSV files there yourself. The geolocation file is not used.

Every column is loaded as text, exactly as in the file; types are set in the dbt staging
models. The script checks that each file has the columns the models need.
Run from the data_eng folder (close any other program using the database first):
    uv run python scripts/load_olist.py
    uv run python scripts/load_olist.py --zip "C:/Users/<you>/Downloads/archive.zip"
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "olist"
DB = ROOT / "data" / "olist.duckdb"

# raw table -> (file name, columns the dbt models use)
FILES = {
    "orders": ("olist_orders_dataset.csv",
               ["order_id", "customer_id", "order_status", "order_purchase_timestamp", "order_approved_at",
                "order_delivered_carrier_date", "order_delivered_customer_date",
                "order_estimated_delivery_date"]),
    "customers": ("olist_customers_dataset.csv",
                  ["customer_id", "customer_unique_id", "customer_zip_code_prefix", "customer_city",
                   "customer_state"]),
    "order_items": ("olist_order_items_dataset.csv",
                    ["order_id", "order_item_id", "product_id", "seller_id", "shipping_limit_date", "price",
                     "freight_value"]),
    "order_payments": ("olist_order_payments_dataset.csv",
                       ["order_id", "payment_sequential", "payment_type", "payment_installments",
                        "payment_value"]),
    "order_reviews": ("olist_order_reviews_dataset.csv",
                      ["review_id", "order_id", "review_score", "review_creation_date",
                       "review_answer_timestamp"]),
    "products": ("olist_products_dataset.csv",
                 ["product_id", "product_category_name", "product_name_lenght", "product_description_lenght",
                  "product_photos_qty", "product_weight_g", "product_length_cm", "product_height_cm",
                  "product_width_cm"]),
    "sellers": ("olist_sellers_dataset.csv",
                ["seller_id", "seller_zip_code_prefix", "seller_city", "seller_state"]),
    "category_translation": ("product_category_name_translation.csv",
                             ["product_category_name", "product_category_name_english"]),
}


def extract(zip_path: Path) -> None:
    wanted = {name for name, _ in FILES.values()}
    with zipfile.ZipFile(zip_path) as zf:
        found = {Path(n).name: n for n in zf.namelist() if Path(n).name in wanted}
        missing = wanted - set(found)
        if missing:
            raise SystemExit(f"{zip_path.name} does not contain: {', '.join(sorted(missing))}")
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        for short, member in found.items():
            (RAW_DIR / short).write_bytes(zf.read(member))
    print(f"Extracted {len(found)} files from {zip_path} to {RAW_DIR.relative_to(ROOT)}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--zip", type=Path, help="Kaggle zip file (default: any .zip in data/raw/olist/)")
    args = p.parse_args()

    missing = [f for f, _ in FILES.values() if not (RAW_DIR / f).exists()]
    if missing:
        zip_path = args.zip or next(iter(sorted(RAW_DIR.glob("*.zip"))), None)
        if zip_path is None or not zip_path.exists():
            print(f"Missing CSV files in {RAW_DIR.relative_to(ROOT)}: {', '.join(missing)}")
            print("Download the dataset from https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce "
                  "and put the zip file in data/raw/olist/ (or pass --zip).")
            return 1
        extract(zip_path)

    DB.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB))
    problems = []
    try:
        con.execute("create schema if not exists raw")
        for table, (file_name, needed) in FILES.items():
            path = (RAW_DIR / file_name).as_posix().replace("'", "''")
            con.execute(f"create or replace table raw.{table} as "
                        f"select * from read_csv('{path}', header = true, all_varchar = true)")
            cols = [r[0] for r in con.execute(f"describe raw.{table}").fetchall()]
            if cols and cols[0].startswith("\ufeff"):          # byte-order mark in the header
                con.execute(f'alter table raw.{table} rename column "{cols[0]}" to "{cols[0][1:]}"')
                cols[0] = cols[0][1:]
            n = con.execute(f"select count(*) from raw.{table}").fetchone()[0]
            lacking = [c for c in needed if c not in cols]
            print(f"  raw.{table:22s} {n:7d} rows  {len(cols):2d} columns"
                  + (f"  MISSING {lacking}" if lacking else ""))
            if lacking:
                problems.append(table)
        con.execute("checkpoint")
    finally:
        con.close()

    if problems:
        print(f"FAIL: files with missing columns: {', '.join(problems)}")
        return 1
    print(f"OK: loaded into {DB.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
