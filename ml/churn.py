"""Customer churn-risk model (classification).

Question: which customers are unlikely to order again in the next 12 months?

Customer snapshots are taken on 1 January of a year. Features use only orders
before the snapshot date; the label is 1 (churned) if the customer places no order
in the following 365 days.
  training snapshots : 2015-01-01 and 2016-01-01 (labels from 2015 and 2016)
  test snapshot      : 2017-01-01 (label from 2017; never used for training or tuning)
  scoring snapshot   : 2018-01-01 (all orders to Dec 2017; label unknown -> prediction)
Candidates (logistic regression, random forest, gradient boosting) are tuned with
GridSearchCV on ROC AUC using StratifiedGroupKFold, grouped by customer so the same
customer never appears in both a training and a validation fold. The model with the
best cross-validated AUC is evaluated once on the 2017 snapshot, refit on all
labelled snapshots, and scores every customer for 2018.
Outputs: models/churn.joblib, results/churn_metrics.json, clean/churn_scores.parquet
"""
import joblib
import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import GridSearchCV, StratifiedGroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

import config as C
from etl.common import get_logger, save_json, timed
from etl.transform import CLEAN

log = get_logger("churn")
NUM = ["recency_days", "tenure_days", "orders", "orders_last_year", "sales", "sales_last_year",
       "avg_order_value", "margin", "avg_discount", "share_technology", "share_furniture"]
CAT = ["segment", "home_region"]


def snapshot(lines, t):
    """Features from orders before t; label = no order in [t, t + 365 days)."""
    t = pd.Timestamp(t)
    past = lines[lines["order_date"] < t]
    yr = past[past["order_date"] >= t - pd.Timedelta(days=365)]
    g = past.groupby("customer_id")
    f = pd.DataFrame({
        "recency_days": (t - g["order_date"].max()).dt.days,
        "tenure_days": (t - g["order_date"].min()).dt.days,
        "orders": g["order_id"].nunique(),
        "sales": g["sales"].sum(),
        "profit": g["profit"].sum(),
        "avg_discount": g["discount"].mean(),
        "segment": g["segment"].first(),
        "home_region": g["region"].agg(lambda s: s.mode().iat[0]),
    })
    f["orders_last_year"] = yr.groupby("customer_id")["order_id"].nunique().reindex(f.index).fillna(0)
    f["sales_last_year"] = yr.groupby("customer_id")["sales"].sum().reindex(f.index).fillna(0)
    f["avg_order_value"] = f["sales"] / f["orders"]
    f["margin"] = f["profit"] / f["sales"]
    cat = past.pivot_table(index="customer_id", columns="category", values="sales", aggfunc="sum").fillna(0)
    f["share_technology"] = (cat.get("Technology", 0) / f["sales"]).fillna(0)
    f["share_furniture"] = (cat.get("Furniture", 0) / f["sales"]).fillna(0)
    nxt = lines[(lines["order_date"] >= t) & (lines["order_date"] < t + pd.Timedelta(days=365))]
    f["churned"] = (~f.index.isin(nxt["customer_id"])).astype(int)
    f["snapshot"] = t
    return f.reset_index()


def pipeline(est, scale=False):
    pre = ColumnTransformer([("num", StandardScaler() if scale else "passthrough", NUM),
                             ("cat", OneHotEncoder(handle_unknown="ignore"), CAT)])
    return Pipeline([("prep", pre), ("model", est)])


CANDIDATES = {
    "logistic_regression": (pipeline(LogisticRegression(max_iter=2000, class_weight="balanced"), True),
                            {"model__C": [0.01, 0.1, 1, 10]}),
    "random_forest": (pipeline(RandomForestClassifier(class_weight="balanced", random_state=C.SEED)),
                      {"model__n_estimators": [400], "model__max_depth": [3, 5, 8],
                       "model__min_samples_leaf": [5, 20]}),
    "gradient_boosting": (pipeline(GradientBoostingClassifier(random_state=C.SEED)),
                          {"model__n_estimators": [100, 300], "model__learning_rate": [0.03, 0.1],
                           "model__max_depth": [2, 3]}),
}


def top_band_stats(y, p, frac=0.2):
    k = int(round(len(p) * frac))
    idx = np.argsort(-p)[:k]
    prec = float(np.mean(np.asarray(y)[idx]))
    return {"flagged": k, "precision": round(prec, 4), "recall": round(float(np.asarray(y)[idx].sum() / np.sum(y)), 4),
            "lift": round(prec / float(np.mean(y)), 2)}


def main():
    with timed(log, "train churn"):
        lines = pd.read_parquet(CLEAN / "fact_order_lines.parquet")
        train = pd.concat([snapshot(lines, "2015-01-01"), snapshot(lines, "2016-01-01")], ignore_index=True)
        test = snapshot(lines, "2017-01-01")
        score_now = snapshot(lines, "2018-01-01")
        log.info("train %d rows (churn %.1f%%), test %d rows (churn %.1f%%), scoring %d customers",
                 len(train), 100 * train["churned"].mean(), len(test), 100 * test["churned"].mean(), len(score_now))

        cv = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=C.SEED)
        results, fitted = {}, {}
        for name, (pipe, grid) in CANDIDATES.items():
            gs = GridSearchCV(pipe, grid, cv=cv, scoring="roc_auc", n_jobs=-1)
            gs.fit(train[NUM + CAT], train["churned"], groups=train["customer_id"])
            p = gs.best_estimator_.predict_proba(test[NUM + CAT])[:, 1]
            results[name] = {"cv_roc_auc": round(float(gs.best_score_), 4),
                             "best_params": {k.replace("model__", ""): v for k, v in gs.best_params_.items()},
                             "test_roc_auc": round(roc_auc_score(test["churned"], p), 4),
                             "test_pr_auc": round(average_precision_score(test["churned"], p), 4)}
            fitted[name] = (gs.best_estimator_, p)
            log.info("%-20s %s", name, results[name])

        best = max(fitted, key=lambda n: results[n]["cv_roc_auc"])
        best_pipe, p_test = fitted[best]
        rec_only = roc_auc_score(test["churned"], test["recency_days"])   # simple baseline: older last order = riskier
        evaluation = {"roc_auc": results[best]["test_roc_auc"], "pr_auc": results[best]["test_pr_auc"],
                      "base_rate": round(float(test["churned"].mean()), 4),
                      "brier": round(brier_score_loss(test["churned"], p_test), 4) if best != "logistic_regression"
                      else None,
                      "top20pct": top_band_stats(test["churned"], p_test),
                      "baseline_recency_only_roc_auc": round(float(rec_only), 4)}
        log.info("selected %s: 2017 test %s", best, evaluation)

        final = clone(best_pipe).fit(pd.concat([train, test])[NUM + CAT], pd.concat([train, test])["churned"])
        joblib.dump(final, C.MODELS / "churn.joblib")
        score_now["churn_probability"] = final.predict_proba(score_now[NUM + CAT])[:, 1].round(4)
        r = score_now["churn_probability"].rank(pct=True)
        score_now["risk_band"] = np.select([r > 0.8, r > 0.5], ["High", "Medium"], "Low")
        cust = pd.read_parquet(CLEAN / "dim_customer.parquet")[["customer_id", "customer_name", "home_state"]]
        out = score_now.merge(cust, on="customer_id", how="left")
        out = out[["customer_id", "customer_name", "segment", "home_region", "home_state", "recency_days",
                   "tenure_days", "orders", "orders_last_year", "sales", "sales_last_year", "margin",
                   "avg_discount", "churn_probability", "risk_band"]].round(4)
        out.to_parquet(CLEAN / "churn_scores.parquet", index=False)

        bands = out.groupby("risk_band").agg(customers=("customer_id", "count"),
                                             sales_last_year=("sales_last_year", "sum")).round(0)
        metrics = {"selected_model": best, "selection_rule": "highest cross-validated ROC AUC (grouped by customer)",
                   "train_rows": len(train), "test_rows": len(test), "candidates": results,
                   "test_evaluation_2017": evaluation, "scored_customers_2018": len(out),
                   "risk_bands_2018": bands.to_dict(orient="index")}
        save_json(metrics, C.RESULTS / "churn_metrics.json")
        log.info("risk bands 2018: %s", metrics["risk_bands_2018"])
        return metrics


if __name__ == "__main__":
    main()
