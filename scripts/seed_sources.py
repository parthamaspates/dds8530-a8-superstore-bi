"""Create the two internal source systems from the original Superstore export.

* CSV source  - a flat-file archive of 2014-2016 orders, exactly as exported
  (US date strings, numeric-looking postal codes).
* SQL source  - an operational database for 2017 in three normalized tables
  (orders, customers, products), as an order-entry system would store it.

Run once (or again to reset):  python scripts/seed_sources.py
"""
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

raw = pd.read_csv(C.RAW_FILE, encoding="latin-1", dtype=str)
year = pd.to_datetime(raw["Order Date"], format="%m/%d/%Y").dt.year

legacy = raw[year <= 2016]
legacy.to_csv(C.LEGACY_CSV, index=False)

ops = raw[year == 2017].copy()
ops["order_date"] = pd.to_datetime(ops["Order Date"], format="%m/%d/%Y").dt.strftime("%Y-%m-%d")
ops["ship_date"] = pd.to_datetime(ops["Ship Date"], format="%m/%d/%Y").dt.strftime("%Y-%m-%d")

orders = ops.rename(columns={"Row ID": "row_id", "Order ID": "order_id", "Ship Mode": "ship_mode",
                             "Customer ID": "customer_id", "Product ID": "product_id",
                             "Country": "country", "City": "city", "State": "state",
                             "Postal Code": "postal_code", "Region": "region", "Sales": "sales",
                             "Quantity": "quantity", "Discount": "discount", "Profit": "profit"})
orders = orders[["row_id", "order_id", "order_date", "ship_date", "ship_mode", "customer_id",
                 "product_id", "country", "city", "state", "postal_code", "region",
                 "sales", "quantity", "discount", "profit"]]
for c in ("sales", "discount", "profit"):
    orders[c] = orders[c].astype(float)
orders["quantity"] = orders["quantity"].astype(int)
orders["row_id"] = orders["row_id"].astype(int)
orders["postal_code"] = orders["postal_code"].astype(int)       # stored as INTEGER, as many systems do

customers = (raw[["Customer ID", "Customer Name", "Segment"]].drop_duplicates("Customer ID")
             .rename(columns={"Customer ID": "customer_id", "Customer Name": "customer_name",
                              "Segment": "segment"}))
products = (ops[["Product ID", "Category", "Sub-Category", "Product Name"]].drop_duplicates()
            .rename(columns={"Product ID": "product_id", "Category": "category",
                             "Sub-Category": "sub_category", "Product Name": "product_name"}))

eng = create_engine(C.SOURCE_DB_URL)
with eng.begin() as con:
    orders.to_sql("orders", con, if_exists="replace", index=False)
    customers.to_sql("customers", con, if_exists="replace", index=False)
    products.to_sql("products", con, if_exists="replace", index=False)

print(f"CSV source : {C.LEGACY_CSV}  ({len(legacy):,} order lines, 2014-2016)")
print(f"SQL source : {C.SOURCE_DB_URL}  orders={len(orders):,}, customers={len(customers):,}, "
      f"products={len(products):,} (2017)")
