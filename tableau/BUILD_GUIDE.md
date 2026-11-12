# Building the Tableau dashboard (Tableau Public)

The pipeline writes `tableau/data/superstore_bi.xlsx` with four sheets:

| Sheet | Grain | Used for |
|---|---|---|
| `Orders` | one row per order line (2014–2017) | KPIs, profitability, discounts |
| `Forecast` | one row per Category × Region × month (2014–2018) | actual vs. predicted, 2018 outlook |
| `States` | one row per state | sales, margin and share of loss-making lines by state |
| `Model_Metrics` | one row per model | forecast accuracy panel |

Install **Tableau Public** (free): <https://public.tableau.com/app/discover> → *Create* → *Download Tableau Public*.

## 1. Connect the data (4 data sources)

1. Open Tableau Public → *Connect* → **Microsoft Excel** → choose `tableau/data/superstore_bi.xlsx`.
2. Drag **Orders** onto the canvas. Rename the data source (top left) to `Orders`.
3. *Data* menu → **New Data Source** → Microsoft Excel → same file → drag **Forecast**. Rename it `Forecast`.
4. Repeat for **States** and **Model_Metrics**.
5. In each source, check the types: `order_date`, `order_month`, `month` = Date; `region`, `category`, `state` = String. In `Orders` and `States`, right-click **state** → *Geographic Role* → **State/Province**.

Using one source per sheet keeps each table at its own grain. The common field names (`region`, `category`) let one filter drive several sources.

## 2. Worksheets

**Sheet 1 – KPIs** (`Orders`)
- Drag `sales`, `profit` to *Text*; create calculated field **Margin** = `SUM([profit]) / SUM([sales])` (format as %). Use *Show Me → Text table*. Format large numbers ($K).

**Sheet 2 – Actual vs. forecast** (`Forecast`)
- Columns: `month` → right-click → **Month** (continuous, green).
- Rows: `actual_sales` (SUM). Drag `predicted` onto the same axis (Measure Values appear) — keep `actual_sales`, `predicted`, `lower_80`, `upper_80`.
- Color by *Measure Names*: actual = dark blue, predicted = orange, lower/upper = light grey (dashed: click Path on the Marks card).
- Filters: `region`, `category` → *Show Filter*. Add `period` to Color or Tooltip.
- Title: "Monthly sales: actual (2014–2017), backtest (2017) and forecast (2018) with 80% band".

**Sheet 3 – 2018 forecast by region and category** (`Forecast`)
- Filter `period` = *Forecast 2018*.
- Rows: `region`; Columns: `category`; Text and Color: SUM(`predicted`) (highlight table).

**Sheet 4 – Profitability by state** (`States`)
- Double-click `state` (map appears). Color: AVG(`margin`) (diverging red–blue, centre 0); Size or Label: SUM(`sales`). Tooltip: `sales`, `profit`, `customers`, `loss_line_share`.

**Sheet 5 – Profit by sub-category** (`Orders`)
- Rows: `sub_category`; Columns: SUM(`profit`), sort descending. Color: the **Margin** field (diverging red–blue, centre 0).

**Sheet 6 – Discount vs. margin** (`Orders`)
- Columns: `discount_band`; Rows: **Margin**; Color: `discount_band`. Add `is_loss` (AVG, as %) to Tooltip — the share of loss-making lines.

**Sheet 7 – Model accuracy** (`Model_Metrics`)
- Rows: `model`; Columns: `wape_series_2017` (bar, as %), Color: `selected`. Title "Forecast error on 2017 (lower is better)".

## 3. Dashboard

1. *New Dashboard* → Size **Automatic** (or 1400 × 900).
2. Layout: KPIs across the top; Sheet 2 large on the left; Sheets 3 and 7 under it; Sheets 4, 5, 6 on the right.
3. Make the `region` and `category` filters global: on each filter card ▸ *Apply to Worksheets* → **All Using Related Data Sources**. If Tableau asks to link the fields, map `region` ↔ `region` and `category` ↔ `category` (*Data → Edit Blend Relationships*).
4. Dashboard action: *Dashboard → Actions → Add Action → Filter* — source Sheet 4 (map), target Sheet 5 and Sheet 6, run on **Select**. Clicking a state then shows its sub-category profit and discount pattern.
5. Add a text box: "Data refreshed: <refreshed_at>" (drag `refreshed_at` from any source onto a small sheet) and a one-line note on the forecast (model, 2017 error).

## 4. Save, publish, refresh

- *File → Save to Tableau Public As…* (sign in with a free account). Tableau Public publishes the workbook and opens it in the browser; copy the URL for the report.
- To submit a file as well: on the Tableau Public page, *Download* → **Tableau Workbook** (`.twbx`), or *File → Save As* locally if your version allows it.
- After each pipeline run (Airflow or cron), the Excel file is rewritten in place. In Tableau, *Data → Refresh* (or reopen) and re-save to Tableau Public to update the online dashboard.
