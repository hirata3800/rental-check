import streamlit as st
import pdfplumber
import pandas as pd
import re
from io import BytesIO

# ==========================================================
# Streamlit 設定
# ==========================================================
st.set_page_config(page_title="レンタル伝票チェックツール（完全版）", layout="wide")

st.title("📄 レンタル伝票チェックツール（完全版）")
st.caption("今回請求額 = 0 の行は必ず備考として直前の利用者へ連結します。")


# ==========================================================
# PDF → 行抽出
# ==========================================================
def extract_rows_from_pdf(file):
    """
    PDF を行レベルで抽出し、行ごとに配列として返す。
    この時点では列数は不定だが、解析側で6列に揃える。
    """
    rows = []

    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:

            # まずテーブル抽出を試す
            try:
                tables = page.extract_tables()
                if tables:
                    for tbl in tables:
                        for row in tbl:
                            if not row:
                                continue
                            row = [str(c).strip() if c else "" for c in row]
                            rows.append(row)
                    continue
            except:
                pass

            # テキスト抽出 fallback
            text = page.extract_text()
            if text:
                for line in text.split("\n"):
                    cols = re.split(r"\s{2,}", line.strip())
                    rows.append(cols)

    return rows


# ==========================================================
# 最終仕様に完全最適化した parse_rows
# ==========================================================
def parse_rows(rows):
    """
    最終的な明確な仕様：
      ・今回金額（列 index=4）が 0 → この行は備考
      ・今回金額 > 0 → この行は利用者
      ・備考行は必ず直前の利用者に連結

    rows は最大で6列必要（ID / NAME / CYCLE / REMARKS / CURRENT / PREV）
    """
    normalized = []
    for r in rows:
        r = r + [""] * 6
        normalized.append(r[:6])

    records = []
    last_user = None

    for row in normalized:
        id_raw = row[0].strip()
        name_raw = row[1].strip()
        cycle = row[2].strip()
        remark_raw = row[3].strip()

        # 今回金額
        try:
            current = int(row[4].replace(",", "").strip())
        except:
            current = 0

        # ------------------------------
        # ★利用者判定は current > 0 のみ★
        # ------------------------------

        if current == 0:
            # ← 備考行
            if last_user:
                extra = f"{id_raw} {name_raw}".strip()
                if remark_raw:
                    extra += " " + remark_raw
                last_user["remarks"].append(extra)
            continue

        # ← 現在行は利用者行
        rec = {
            "id": id_raw,
            "name": name_raw,
            "cycle": cycle,
            "remarks": [],
            "amount_val": current
        }

        if remark_raw:
            rec["remarks"].append(remark_raw)

        records.append(rec)
        last_user = rec

    df = pd.DataFrame([{
        "id": r["id"],
        "name": r["name"],
        "cycle": r["cycle"],
        "remarks": " / ".join(r["remarks"]),
        "amount_val": r["amount_val"]
    } for r in records])

    return df


# ==========================================================
# 入力 UI
# ==========================================================
col1, col2 = st.columns(2)
with col1:
    cur_file = st.file_uploader("① 今回請求分（Current）PDF", type=["pdf"])
with col2:
    prev_file = st.file_uploader("② 前回請求分（Previous）PDF", type=["pdf"])

if not cur_file:
    st.stop()


# ==========================================================
# PDF 解析
# ==========================================================
with st.spinner("PDF解析中..."):
    cur_rows_raw = extract_rows_from_pdf(BytesIO(cur_file.read()))
    df_current = parse_rows(cur_rows_raw)

    if prev_file:
        prev_rows_raw = extract_rows_from_pdf(BytesIO(prev_file.read()))
        df_prev = parse_rows(prev_rows_raw)
    else:
        df_prev = pd.DataFrame(columns=["id", "name", "cycle", "remarks", "amount_val"])


# ==========================================================
# 比較処理
# ==========================================================
df_current = df_current.drop_duplicates(subset=["id"])
df_prev = df_prev.drop_duplicates(subset=["id"])

merged = pd.merge(
    df_current,
    df_prev[["id", "amount_val"]],
    on="id",
    how="left",
    suffixes=("_curr", "_prev")
)

merged["is_new"] = merged["amount_val_prev"].isna()
merged["is_diff"] = (~merged["is_new"]) & (merged["amount_val_curr"] != merged["amount_val_prev"])
merged["is_same"] = (~merged["is_new"]) & (merged["amount_val_curr"] == merged["amount_val_prev"])


# ==========================================================
# 表示用整形
# ==========================================================
view = merged.copy()

view["今回請求額"] = view["amount_val_curr"].apply(lambda v: f"{v:,}")
view["前回請求額"] = view["amount_val_prev"].apply(
    lambda v: f"{int(v):,}" if pd.notnull(v) else "該当なし"
)

display = view[[
    "id", "name", "cycle", "remarks",
    "今回請求額", "前回請求額",
    "is_new", "is_diff", "is_same"
]].copy()

display.columns = [
    "ID", "利用者名", "請求サイクル", "備考",
    "今回請求額", "前回請求額",
    "is_new", "is_diff", "is_same"
]


# ==========================================================
# ハイライト
# ==========================================================
def highlight(row):
    styles = ['background-color:white; color:black;'] * len(row)

    if row["is_new"]:
        styles[4] = "background-color:#ffe6e6; color:red; font-weight:bold;"
    elif row["is_diff"]:
        styles[4] = "background-color:#ffe6e6; color:red; font-weight:bold;"
        styles[5] = "color:blue; font-weight:bold;"
    elif row["is_same"]:
        styles[4] = "color:#999;"
        styles[5] = "color:#999;"

    return styles


st.markdown("### 📊 判定結果（金額0行は全て自動的に備考へ統合）")
st.dataframe(display.style.apply(highlight, axis=1), use_container_width=True, height=700)

st.download_button(
    "CSVダウンロード",
    display.to_csv(index=False).encode("utf-8-sig"),
    "check_result.csv"
)
