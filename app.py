import streamlit as st
import pdfplumber
import pandas as pd
import re
from io import BytesIO

# ==========================================================
# ページ設定
# ==========================================================
st.set_page_config(page_title="レンタル伝票チェックツール（完全版）", layout="wide")

st.title("📄 レンタル伝票チェックツール（完全版・金額0行補正）")
st.caption("今回請求額 = 0 の行は、自動的に直前の利用者の備考に統合します。")

# ==========================================================
# PDF → 行データ抽出
# ==========================================================

def extract_rows_from_pdf(file):
    """
    PDFの全行を抽出して、表でもテキストでも行として返す。
    """
    rows = []
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            # テーブル抽出
            try:
                tables = page.extract_tables()
                if tables:
                    for tbl in tables:
                        for row in tbl:
                            if row:
                                rows.append([str(c).strip() if c else "" for c in row])
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
# あなた用に完全最適化した解析関数
# ==========================================================

def parse_rows(rows):
    """
    rows は以下のようなリスト形式：
    [
      ["0000011158", "黒﨑誠", "6ヶ月", "備考", "7200", "8700"],
      ["0100143486", "藤吉様の奥様(同時に集金→回収)", "", "", "0", "0"],
      ...
    ]

    金額0の行は直前の利用者の備考に吸収する。
    """

    clean_rows = []

    # 行長を 6 列に揃える（ID / NAME / CYCLE / REMARK / CURRENT / PREV）
    for r in rows:
        r = r + [""] * 6
        clean_rows.append(r[:6])

    records = []
    last = None

    for r in clean_rows:
        id_raw = r[0].strip()
        name_raw = r[1].strip()
        cycle = r[2].strip()
        remark = r[3].strip()

        # 金額（今回）
        try:
            current = int(r[4].replace(",", ""))
        except:
            current = 0

        # -------------- ルール①：金額0 → 直前利用者の備考行 ----------------
        if current == 0:
            if last:
                extra = f"{id_raw} {name_raw}".strip()
                if remark:
                    extra += f" {remark}"
                last["remarks"].append(extra)
            continue

        # -------------- ルール②：金額0でない → 新しい利用者行 --------------
        rec = {
            "id": id_raw,
            "name": name_raw,
            "cycle": cycle,
            "remarks": [],
            "amount_val": current
        }

        # もと備考欄があるなら追加
        if remark:
            rec["remarks"].append(remark)

        records.append(rec)
        last = rec

    # DataFrame化
    df = pd.DataFrame([{
        "id": r["id"],
        "name": r["name"],
        "cycle": r["cycle"],
        "remarks": " / ".join(r["remarks"]),
        "amount_val": r["amount_val"]
    } for r in records])

    return df


# ==========================================================
# 入力 UI（今回・前回）
# ==========================================================

col1, col2 = st.columns(2)

with col1:
    cur_file = st.file_uploader("① 今回請求分 (Current)", type=["pdf"])
with col2:
    prev_file = st.file_uploader("② 前回請求分 (Previous)", type=["pdf"])


if not cur_file:
    st.stop()

# ==========================================================
# PDF 読み込み・解析
# ==========================================================
with st.spinner("PDF解析中..."):

    cur_rows_raw = extract_rows_from_pdf(BytesIO(cur_file.read()))

    st.write("抽出された行データ（今回）:", cur_rows_raw[:50])
    
    df_current = parse_rows(cur_rows_raw)

    if prev_file:
        prev_rows_raw = extract_rows_from_pdf(BytesIO(prev_file.read()))
        df_prev = parse_rows(prev_rows_raw)
    else:
        df_prev = pd.DataFrame(columns=["id", "name", "cycle", "remarks", "amount_val"])


# ==========================================================
# 差異判定
# ==========================================================
if df_current.empty:
    st.error("今回分から利用者が検出できませんでした。")
    st.stop()

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

# 表示用整形
view = merged.copy()
view["今回請求額"] = view["amount_val_curr"].apply(lambda v: f"{v:,}")
view["前回請求額"] = merged["amount_val_prev"].apply(lambda v: f"{int(v):,}" if pd.notnull(v) else "該当なし")

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
# 表示（ハイライト）
# ==========================================================

def highlight(row):
    styles = ['background-color:white; color:black;'] * len(row)

    # 新規
    if row["is_new"]:
        styles[4] = "background-color:#ffe6e6; color:red; font-weight:bold;"

    # 変更
    elif row["is_diff"]:
        styles[4] = "background-color:#ffe6e6; color:red; font-weight:bold;"
        styles[5] = "color:blue; font-weight:bold;"

    # 一致
    elif row["is_same"]:
        styles[4] = styles[5] = "color:#999;"

    return styles

st.markdown("### 判定結果")
st.dataframe(display.style.apply(highlight, axis=1), use_container_width=True, height=700)

st.download_button(
    "CSVダウンロード",
    display.to_csv(index=False).encode("utf-8-sig"),
    "check_result.csv"
)

