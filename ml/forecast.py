"""Sales forecasting model (regression).

Forecasts monthly sales for each Category x Region series (3 x 4 = 12 series) up to
12 months ahead with one global scikit-learn model.

Design: a *direct* 12-month-ahead model. Every feature is known at least 12 months
before the month being forecast, so the same model can forecast a whole year without
feeding its own predictions back in:
  lag12     sales in the same month one year earlier
  level12   average monthly sales over the 12 months ending one year earlier
  month_num month of year (seasonality)
  holidays  number of US public holidays in the month (from the holiday API)
  category, region (one-hot)
Candidates (Ridge, random forest, gradient boosting) are tuned with GridSearchCV over
a TimeSeriesSplit on the training period only. All candidates are then backtested on
the full year 2017 against two naive baselines. The model with the lowest
cross-validated error is selected (2017 is kept out of the selection), refit on all
labelled data, and forecasts Jan-Dec 2018. The 80%
interval comes from the 10th/90th percentiles of the backtest actual/predicted ratios.
Outputs: models/forecast.joblib, results/forecast_metrics.json, clean/forecast.parquet
"""
import joblib
import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.model_selection import GridSearchCV, TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

import config as C
from etl.common import get_logger, save_json, timed
from etl.transform import CLEAN

log = get_logger("forecast")
NUM = ["lag12", "level12", "month_num", "holidays"]
CAT = ["category", "region"]
TEST_YEAR = 2017


def build_features(ms, holidays, until):
    """Feature rows for every series-month up to `until` (future months get NaN sales)."""
    months = pd.date_range(ms["month"].min(), until, freq="MS")
    grid = pd.MultiIndex.from_product([sorted(ms["category"].unique()), sorted(ms["region"].unique()),
                                       months], names=["category", "region", "month"]).to_frame(index=False)
    df = grid.merge(ms[["category", "region", "month", "sales"]], how="left")
    df = df.sort_values(["category", "region", "month"])
    g = df.groupby(["category", "region"])["sales"]
    df["lag12"] = g.shift(12)
    df["level12"] = g.transform(lambda s: s.shift(12).rolling(12).mean())
    df["month_num"] = df["month"].dt.month
    df["holidays"] = df["month"].map(holidays).fillna(0)
    return df.dropna(subset=["lag12", "level12"]).reset_index(drop=True)


def pipeline(est, scale=False):
    pre = ColumnTransformer([("num", StandardScaler() if scale else "passthrough", NUM),
                             ("cat", OneHotEncoder(handle_unknown="ignore"), CAT)])
    return Pipeline([("prep", pre), ("model", est)])


CANDIDATES = {
    "ridge": (pipeline(Ridge(), scale=True), {"model__alpha": [0.1, 1, 10, 100]}),
    "random_forest": (pipeline(RandomForestRegressor(random_state=C.SEED)),
                      {"model__n_estimators": [300], "model__max_depth": [3, 4, 6],
                       "model__min_samples_leaf": [3, 5, 10]}),
    "gradient_boosting": (pipeline(GradientBoostingRegressor(random_state=C.SEED)),
                          {"model__n_estimators": [100, 200], "model__learning_rate": [0.03, 0.1],
                           "model__max_depth": [2, 3]}),
}


def wape(actual, pred):
    """Weighted absolute percentage error: sum|error| / sum(actual)."""
    return float(np.abs(np.asarray(actual) - np.asarray(pred)).sum() / np.abs(actual).sum())


def score(df, pred):
    tot = df.assign(p=pred).groupby("month")[["sales", "p"]].sum()
    return {"wape_series": round(wape(df["sales"], pred), 4),
            "wape_company_monthly": round(wape(tot["sales"], tot["p"]), 4),
            "predicted_total": round(float(np.sum(pred)), 0)}


def main():
    with timed(log, "train forecast"):
        ms = pd.read_parquet(CLEAN / "monthly_sales.parquet")
        hol = pd.read_parquet(CLEAN / "holidays.parquet")
        holidays = (pd.to_datetime(hol["date"]).dt.to_period("M").dt.to_timestamp()
                    .value_counts().astype(float))
        last = ms["month"].max()
        feat = build_features(ms, holidays, last + pd.DateOffset(months=12))
        labelled = feat[feat["sales"].notna()]
        train = labelled[labelled["month"].dt.year < TEST_YEAR].sort_values("month")
        test = labelled[labelled["month"].dt.year == TEST_YEAR]
        log.info("training rows %d (%s to %s), test rows %d", len(train), train["month"].min().date(),
                 train["month"].max().date(), len(test))

        results, fitted = {}, {}
        cv = TimeSeriesSplit(n_splits=3)
        for name, (pipe, grid) in CANDIDATES.items():
            gs = GridSearchCV(pipe, grid, cv=cv, scoring="neg_mean_absolute_error", n_jobs=-1)
            gs.fit(train[NUM + CAT], train["sales"])
            pred = np.clip(gs.best_estimator_.predict(test[NUM + CAT]), 0, None)
            results[name] = {"cv_mae": round(float(-gs.best_score_), 1),
                             "best_params": {k.replace("model__", ""): v for k, v in gs.best_params_.items()},
                             **score(test, pred)}
            fitted[name] = (gs.best_estimator_, pred)
            log.info("%-18s %s", name, results[name])
        results["seasonal_naive"] = score(test, test["lag12"].to_numpy())
        results["last_year_average"] = score(test, test["level12"].to_numpy())
        for b in ("seasonal_naive", "last_year_average"):
            log.info("%-18s %s", b, results[b])

        best = min(fitted, key=lambda n: results[n]["cv_mae"])
        best_pipe, back_pred = fitted[best]
        ratio = (test["sales"] / back_pred).replace([np.inf], np.nan).dropna()
        lo_q, hi_q = float(ratio.quantile(0.10)), float(ratio.quantile(0.90))

        final = clone(best_pipe).fit(labelled[NUM + CAT], labelled["sales"])
        future = feat[feat["sales"].isna()].copy()
        future["predicted"] = np.clip(final.predict(future[NUM + CAT]), 0, None)
        joblib.dump(final, C.MODELS / "forecast.joblib")

        # one row per series-month: actual (2014-2017) and predicted (2017 backtest, 2018 forecast)
        hist = ms[["category", "region", "month", "sales"]].rename(columns={"sales": "actual_sales"})
        bt = test[["category", "region", "month"]].assign(predicted=back_pred)
        fwd = future[["category", "region", "month", "predicted"]]
        out = hist.merge(pd.concat([bt, fwd]), on=["category", "region", "month"], how="outer")
        out["lower_80"] = out["predicted"] * lo_q
        out["upper_80"] = out["predicted"] * hi_q
        out["period"] = np.select([out["month"].dt.year == TEST_YEAR, out["month"] > last],
                                  [f"Backtest {TEST_YEAR}", f"Forecast {last.year + 1}"], "History")
        out["abs_error"] = (out["actual_sales"] - out["predicted"]).abs()
        num = ["actual_sales", "predicted", "lower_80", "upper_80", "abs_error"]
        out[num] = out[num].round(2)
        out = out.sort_values(["category", "region", "month"]).reset_index(drop=True)
        out.to_parquet(CLEAN / "forecast.parquet", index=False)

        metrics = {"selected_model": best, "selection_rule": "lowest cross-validated MAE on the training period; 2017 is reported, not used for selection",
                   "train_rows": len(train), "test_rows": len(test),
                   "interval_80pct_ratio_p10_p90": [round(lo_q, 3), round(hi_q, 3)],
                   "actual_2017_total": round(float(test["sales"].sum()), 0),
                   "forecast_2018_total": round(float(future["predicted"].sum()), 0),
                   "candidates": results}
        save_json(metrics, C.RESULTS / "forecast_metrics.json")
        log.info("selected %s; 2018 forecast %.0f vs 2017 actual %.0f; interval x%.2f-x%.2f", best,
                 metrics["forecast_2018_total"], metrics["actual_2017_total"], lo_q, hi_q)
        return metrics


if __name__ == "__main__":
    main()
