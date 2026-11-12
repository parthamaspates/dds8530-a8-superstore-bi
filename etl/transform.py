"""TRANSFORM: clean, integrate and enrich the staged data with pandas.

Steps
  1. Harmonize the CSV and SQL schemas (snake_case names, types, date formats).
  2. Resolve product-name conflicts (one product_id, several names) and attach
     product attributes to the SQL order lines.
  3. Repair postal codes that lost their leading zero (e.g. 5408 -> 05408).
  4. Remove exact duplicate order lines; apply validity rules and set failing
     rows aside in a reject table.
  5. Derive analysis fields (margin, shipping days, discount band, holiday week).
  6. Flag orders placed in a public-holiday week (holiday API).
  7. Build the aggregates used by the model and the dashboard.
Outputs go to data/staging/clean/*.parquet plus a data-quality report.
"""
import numpy as np
import pandas as pd

import config as C
from etl.common import get_logger, save_json, timed

log = get_logger("transform")
CLEAN = C.STAGING / "clean"
KEY_COLS = ["order_id", "order_date", "ship_date", "ship_mode", "customer_id", "product_id",
            "city", "state", "postal_code", "region", "sales", "quantity", "discount", "profit"]
CSV_RENAME = {"Row ID": "row_id", "Order ID": "order_id", "Order Date": "order_date",
              "Ship Date": "ship_date", "Ship Mode": "ship_mode", "Customer ID": "customer_id",
              "Customer Name": "customer_name", "Segment": "segment", "Country": "country",
              "City": "city", "State": "state", "Postal Code": "postal_code", "Region": "region",
              "Product ID": "product_id", "Category": "category", "Sub-Category": "sub_category",
              "Product Name": "product_name", "Sales": "sales", "Quantity": "quantity",
              "Discount": "discount", "Profit": "profit"}


def harmonize(csv, sql):
    csv = csv.rename(columns=CSV_RENAME)
    csv["order_date"] = pd.to_datetime(csv["order_date"], format="%m/%d/%Y")
    csv["ship_date"] = pd.to_datetime(csv["ship_date"], format="%m/%d/%Y")
    csv["source"] = "csv_archive"
    sql = sql.copy()
    sql["order_date"] = pd.to_datetime(sql["order_date"], format="%Y-%m-%d")
    sql["ship_date"] = pd.to_datetime(sql["ship_date"], format="%Y-%m-%d")
    sql["source"] = "sql_operations"
    for df in (csv, sql):
        for c in ("sales", "discount", "profit"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
        df["postal_code"] = df["postal_code"].astype(str)
    return csv, sql


def resolve_products(csv, products):
    """One canonical row per product_id; the most frequent name wins."""
    names = pd.concat([csv[["product_id", "category", "sub_category", "product_name"]], products])
    n_conflict = int((names.groupby("product_id")["product_name"].nunique() > 1).sum())
    dim = (names.groupby(["product_id", "category", "sub_category", "product_name"]).size()
           .rename("n").reset_index().sort_values(["product_id", "n", "product_name"],
                                                   ascending=[True, False, True])
           .drop_duplicates("product_id").drop(columns="n"))
    return dim, n_conflict


def main():
    with timed(log, "transform"):
        CLEAN.mkdir(parents=True, exist_ok=True)
        q = {}
        csv = pd.read_parquet(C.STAGING / "csv_orders.parquet")
        sql = pd.read_parquet(C.STAGING / "sql_orders.parquet")
        products = pd.read_parquet(C.STAGING / "sql_products.parquet")
        hol = pd.read_parquet(C.STAGING / "api_holidays.parquet")
        q["rows_in"] = {"csv": len(csv), "sql": len(sql)}

        csv, sql = harmonize(csv, sql)
        dim_product, q["product_ids_with_conflicting_names"] = resolve_products(csv, products)
        q["naive_join_extra_rows"] = int(len(sql.merge(products, on="product_id")) - len(sql))
        sql = sql.merge(dim_product, on="product_id", how="left", validate="many_to_one")
        csv = csv.drop(columns=["product_name"]).merge(dim_product[["product_id", "product_name"]],
                                                       on="product_id", how="left")
        df = pd.concat([csv, sql], ignore_index=True, sort=False)

        short = df["postal_code"].str.len() < 5
        q["postal_codes_zero_padded"] = int(short.sum())
        df["postal_code"] = df["postal_code"].str.zfill(5)

        dup = df.duplicated(subset=KEY_COLS, keep="first")
        q["duplicate_lines_removed"] = int(dup.sum())
        df = df[~dup]

        rules = {
            "missing_value": df[KEY_COLS + ["category", "segment"]].isna().any(axis=1),
            "ship_before_order": df["ship_date"] < df["order_date"],
            "non_positive_sales": df["sales"] <= 0,
            "non_positive_quantity": df["quantity"] <= 0,
            "discount_out_of_range": ~df["discount"].between(0, 0.8),
        }
        bad = np.zeros(len(df), dtype=bool)
        for name, m in rules.items():
            q[f"rule_{name}"] = int(m.sum())
            bad |= m.to_numpy()
        rejects = df[bad]
        df = df[~bad].copy()
        q["rows_rejected"] = int(bad.sum())

        df["quantity"] = df["quantity"].astype(int)
        df["order_month"] = df["order_date"].dt.to_period("M").dt.to_timestamp()
        df["order_year"] = df["order_date"].dt.year
        df["ship_days"] = (df["ship_date"] - df["order_date"]).dt.days
        df["margin"] = (df["profit"] / df["sales"]).round(4)
        df["is_loss"] = (df["profit"] < 0).astype(int)
        df["discount_band"] = pd.cut(df["discount"], [-0.01, 0, 0.2, 0.4, 0.8],
                                     labels=["None", "1-20%", "21-40%", "41-80%"]).astype(str)
        hol["date"] = pd.to_datetime(hol["date"])
        hol_weeks = set(zip(hol["date"].dt.isocalendar().year, hol["date"].dt.isocalendar().week))
        iso = df["order_date"].dt.isocalendar()
        df["holiday_week"] = [int((y, w) in hol_weeks) for y, w in zip(iso["year"], iso["week"])]


        df = df.sort_values(["order_date", "order_id", "product_id"]).reset_index(drop=True)
        df["line_id"] = np.arange(1, len(df) + 1)
        cols = ["line_id", "order_id", "order_date", "order_month", "order_year", "ship_date",
                "ship_mode", "ship_days", "customer_id", "customer_name", "segment", "city", "state",
                "postal_code", "region", "product_id", "category", "sub_category",
                "product_name", "sales", "quantity", "discount", "discount_band", "profit", "margin",
                "is_loss", "holiday_week", "source"]
        fact = df[cols]

        # ---- aggregates -------------------------------------------------------------
        hol_m = (hol.assign(month=hol["date"].dt.to_period("M").dt.to_timestamp())
                 .groupby("month").size().rename("holidays"))
        grid = pd.MultiIndex.from_product([sorted(fact["category"].unique()),
                                           sorted(fact["region"].unique()),
                                           pd.date_range("2014-01-01", "2017-12-01", freq="MS")],
                                          names=["category", "region", "month"]).to_frame(index=False)
        monthly = (fact.groupby(["category", "region", "order_month"])
                   .agg(sales=("sales", "sum"), profit=("profit", "sum"), orders=("order_id", "nunique"))
                   .reset_index().rename(columns={"order_month": "month"}))
        monthly = grid.merge(monthly, how="left").fillna({"sales": 0, "profit": 0, "orders": 0})
        monthly["holidays"] = monthly["month"].map(hol_m).fillna(0).astype(int)

        state = (fact.groupby(["state", "region"])
                 .agg(sales=("sales", "sum"), profit=("profit", "sum"), orders=("order_id", "nunique"),
                      customers=("customer_id", "nunique"), loss_lines=("is_loss", "sum"),
                      lines=("line_id", "count"))
                 .reset_index())
        state["loss_line_share"] = (state["loss_lines"] / state["lines"]).round(4)
        state["sales_per_customer"] = (state["sales"] / state["customers"]).round(2)
        state["margin"] = (state["profit"] / state["sales"]).round(4)

        dim_customer = (fact.sort_values("order_date").groupby("customer_id")
                        .agg(customer_name=("customer_name", "first"), segment=("segment", "first"),
                             first_order=("order_date", "min"), last_order=("order_date", "max"),
                             home_region=("region", lambda s: s.mode().iat[0]),
                             home_state=("state", lambda s: s.mode().iat[0]))
                        .reset_index())

        for name, t in {"fact_order_lines": fact, "dim_product": dim_product, "dim_customer": dim_customer,
                        "monthly_sales": monthly, "state_summary": state, "holidays": hol,
                        "rejects": rejects}.items():
            t.to_parquet(CLEAN / f"{name}.parquet", index=False)
        q["rows_out"] = len(fact)
        q["date_range"] = [str(fact["order_date"].min().date()), str(fact["order_date"].max().date())]
        save_json(q, C.RESULTS / "data_quality.json")
        log.info("quality report: %s", q)
        return q


if __name__ == "__main__":
    main()
