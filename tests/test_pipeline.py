"""Offline tests for the transform and forecasting logic:  python -m pytest -q"""
import numpy as np
import pandas as pd

from etl.transform import resolve_products
from ml.forecast import build_features, wape


def test_resolve_products_picks_most_frequent_name():
    csv = pd.DataFrame({"product_id": ["P1"] * 3 + ["P2"], "category": ["A"] * 4,
                        "sub_category": ["a"] * 4, "product_name": ["X", "X", "Y", "Z"]})
    sql = pd.DataFrame({"product_id": ["P1"], "category": ["A"], "sub_category": ["a"], "product_name": ["Y"]})
    dim, n_conflict = resolve_products(csv, sql)
    assert n_conflict == 1
    assert dim.set_index("product_id")["product_name"].to_dict() == {"P1": "X", "P2": "Z"}
    assert dim["product_id"].is_unique


def test_postal_zero_padding():
    assert pd.Series(["5408", "10024"]).str.zfill(5).tolist() == ["05408", "10024"]


def test_features_use_only_data_a_year_old():
    months = pd.date_range("2014-01-01", "2016-12-01", freq="MS")
    ms = pd.DataFrame({"category": "A", "region": "R", "month": months, "sales": np.arange(len(months), dtype=float)})
    f = build_features(ms, pd.Series(dtype=float), months[-1] + pd.DateOffset(months=12))
    row = f[f["month"] == "2016-06-01"].iloc[0]
    assert row["lag12"] == 17          # value of 2015-06
    assert row["level12"] == np.mean(np.arange(6, 18))   # 2014-07 .. 2015-06
    future = f[f["sales"].isna()]
    assert len(future) == 12 and future[["lag12", "level12"]].notna().all().all()


def test_wape():
    assert wape(np.array([100, 100]), np.array([90, 120])) == 0.15
