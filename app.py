# app.py
import streamlit as st
import pdfplumber
import pandas as pd
import re
from io import BytesIO

st.set_page_config(page_title="請求書チェックツール（頑健版）", layout="wide")

st.title("📄 レンタル伝票 差異チェックツール（頑健版）")
st.caption("PDFのセル分割が崩れていても、行テキスト解析で誤認を減らします。")

# ---------------------------
# 設定（必要ならここを編集）
# ---------------------------
NG_WORDS = [
    "様", "様の", "奥様", "奥", "夫", "妻", "娘", "息子", "息",
    "親", "義", "回収", "集金", "亡", "同時", "回収済", "集金済",
    "ﾚﾝﾀﾙなし", "レンタルなし", "回収→", "集金→"
]

# 金額抽出用正規（カンマあり等に対応）
AMOUNT_RE = re.compile(r'([0-9０-９]{1,3}(?:[,，][0-9]{3})*(?:[0-9]|))(?:\s*円)?$')

# ---------------------------
# ヘルパー関数
# ---------------------------
def normalize_whitespace(s: str) -> str:
    return re.sub(r'\s+', ' ', (s or "").strip())

def to_half_width_digits(s: str) -> str:
    table = str.maketrans('０１２３４５６７８９', '0123456789')
    return s.translate(table)

def extract_amount_from_text(s: str):
    """行末にある金額を拾う。カンマ付き、全角数字対応。"""
    if not s:
        return 0
    s = s.strip()
    s = to_half_width_digits(s)
    # try to find last numeric token (prefer rightmost)
    # Common patterns: "7,200", "7200", "7200円"
    # We check from rightmost contiguous digits with optional commas
    m = re.search(r'([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\s*円)?\s*$', s)
    if m:
        num = m.group(1).replace(',', '')
        try:
            val = int(num)
            # safety cap
            if abs(val) > 500000:
                return 0
            return val
        except:
            return 0
    return 0

def contains_ng_word(s: str):
    if not s: return False
    for ng in NG_WORDS:
        if ng in s:
            return True
    return False

def looks_like_person_name(s: str):
    """簡易：漢字/かなが2文字以上含まれると人名らしいとする"""
    if not s: return False
    # exclude purely numeric or ascii
    if re.search(r'[A-Za-z0-9]', s) and not re.search(r'[\u4e00-\u9fff\u3040-\u30ff]', s):
        return False
    if re.search(r'[\u4e00-\u9fff\u3040-\u30ff]{2,}', s):
        return True
    return False

def normalize_name_text(s: str):
    if s is None: return ""
    # remove parentheses content and trailing arrows etc
    s = re.sub(r'（.*?）', '', s)
    s = re.sub(r'\(.*?\)', '', s)
    s = re.sub(r'→.*', '', s)
    s = s.strip(" -–—:：・、,")
    return s.strip()

# ---------------------------
# パーサー（行ベース、頑健版）
# ---------------------------
def parse_text_lines_to_records(lines):
    """
    lines: list[str] - text lines extracted from PDF pages in reading order
    returns: list of records {id, name, cycle, remarks, amount_val}
    """
    records = []
    last_record = None

    i = 0
    while i < len(lines):
        raw = lines[i].strip()
        if not raw:
            i += 1
            continue

        # Extract amount from this line (prefer rightmost)
        amt = extract_amount_from_text(raw)

        # Find an ID (6+ digits) anywhere in the line
        id_m = re.search(r'(\d{6,})', raw)

        if id_m and amt > 0:
            # Best case: ID and name and amount on same line
            user_id = id_m.group(1)
            # name part is between id end and amount start
            # compute amount start by searching the amount match region
            amt_match = re.search(r'([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\s*円)?\s*$', raw)
            amt_start = amt_match.start() if amt_match else len(raw)
            name_candidate = raw[id_m.end():amt_start].strip()
            name_candidate = normalize_name_text(name_candidate)
            # if name is empty, maybe name is on next line(s)
            if not name_candidate:
                # lookahead for next non-empty line (but avoid consuming lines with amount)
                j = i + 1
                found_name = ""
                while j < len(lines) and len(found_name) < 1:
                    nxt = lines[j].strip()
                    if not nxt:
                        j += 1
                        continue
                    # if next line also contains an amount, probably it's not a name line
                    if extract_amount_from_text(nxt) > 0:
                        break
                    found_name = normalize_name_text(nxt)
                    # consider this line consumed as name
                    if found_name:
                        name_candidate = found_name
                        i = j  # advance index to skip consumed name line
                        break
                    j += 1

            # NG check
            if contains_ng_word(name_candidate):
                # treat whole line as remark (attach to last_record if any)
                if last_record:
                    last_record['remarks'].append(raw)
                else:
                    # whip up a phantom remark-only record? We skip creating a record,
                    # but optionally we can keep a 'unattached remarks' list. For simplicity, skip.
                    pass
                i += 1
                continue

            # name-likeness
            if not looks_like_person_name(name_candidate):
                # if not name-like, attach to last_record remarks
                if last_record:
                    last_record['remarks'].append(raw)
                i += 1
                continue

            # Build record
            rec = {
                "id": user_id,
                "name": name_candidate,
                "cycle": "",  # we'll attempt to detect cycle from surrounding lines/cells later
                "remarks": [],
                "amount_val": amt
            }
            # try to find cycle token in this line or nearby tokens
            cyc = re.search(r'(\d+\s*(?:ヶ月|か月|月|年))', raw)
            if cyc:
                rec['cycle'] = cyc.group(1)
            records.append(rec)
            last_record = rec
            i += 1
            continue

        # Case: ID exists but amount not on same line -> maybe split over multiple lines
        if id_m and amt == 0:
            user_id = id_m.group(1)
            # attempt to find amount in next up-to-3 lines and name either on same line or adjacent
            name_candidate = normalize_name_text(raw[id_m.end():].strip())
            look_ahead_amt = 0
            look_ahead_name = name_candidate
            consumed = 0
            for j in range(1, 4):
                if i + j >= len(lines):
                    break
                nxt = lines[i + j].strip()
                if not nxt:
                    continue
                # if this next line has an amount, use it
                a2 = extract_amount_from_text(nxt)
                if a2 > 0 and look_ahead_amt == 0:
                    look_ahead_amt = a2
                    consumed = j
                    break
                # if next line has name-like text and name empty, pick it
                if not look_ahead_name:
                    cand = normalize_name_text(nxt)
                    if looks_like_person_name(cand) and not contains_ng_word(cand):
                        look_ahead_name = cand
                        consumed = j
                        # continue looking for amount further
                # continue loop
            if look_ahead_amt > 0 and look_ahead_name and not contains_ng_word(look_ahead_name):
                # create record
                rec = {
                    "id": user_id,
                    "name": look_ahead_name,
                    "cycle": "",
                    "remarks": [],
                    "amount_val": look_ahead_amt
                }
                records.append(rec)
                last_record = rec
                i += consumed + 1
                continue
            else:
                # cannot form record: attach to last_record remarks
                if last_record:
                    last_record['remarks'].append(raw)
                i += 1
                continue

        # Case: no ID in this line
        # If line contains an amount but no ID -> ambiguous; attach as remark to last_record
        if amt > 0 and not id_m:
            if last_record:
                last_record['remarks'].append(raw)
            i += 1
            continue

        # No ID and no amount: pure remark
        if last_record:
            last_record['remarks'].append(raw)
        i += 1

    # final dataframe
    final = []
    for r in records:
        final.append({
            "id": r["id"],
            "name": r["name"],
            "cycle": r.get("cycle", ""),
            "remarks": " ".join(r.get("remarks", [])).strip(),
            "amount_val": r.get("amount_val", 0)
        })
    return pd.DataFrame(final)


# ---------------------------
# PDF -> text extraction helpers
# ---------------------------
def extract_lines_from_pdf(file_like):
    """Try table extraction first, then fallback to page text lines.
       Returns list of lines (strings) in reading order.
    """
    lines = []
    # file_like may be BytesIO or UploadedFile
    try:
        with pdfplumber.open(file_like) as pdf:
            for page in pdf.pages:
                # attempt to extract table rows as lines (if table extraction is good)
                try:
                    tables = page.extract_tables(table_settings={
                        "vertical_strategy": "text",
                        "horizontal_strategy": "text",
                        "snap_tolerance": 3
                    })
                    if tables:
                        for table in tables:
                            for row in table:
                                # join non-empty cells with a space — keeps order
                                row_cells = [str(c).strip() for c in row if c is not None and str(c).strip() != ""]
                                if row_cells:
                                    lines.append(" ".join(row_cells))
                        continue  # next page
                except Exception:
                    # ignore and fallback to text
                    pass

                # fallback: extract raw text and split into lines
                text = page.extract_text()
                if text:
                    for t in text.split("\n"):
                        t = t.strip()
                        if t:
                            lines.append(t)
    except Exception as e:
        st.error(f"PDF解析に失敗しました: {e}")
    return lines

# ---------------------------
# Streamlit UI components
# ---------------------------
col1, col2 = st.columns([1, 1])
with col1:
    uploaded1 = st.file_uploader("① 今回請求分 (Current) - PDF", type=["pdf"], key="cur")
with col2:
    uploaded2 = st.file_uploader("② 前回請求分 (Previous) - PDF", type=["pdf"], key="prev")

st.markdown("または、PDFをアップできない場合は下にテキストを貼り付けてください（OCRや別ツールで抽出したテキスト）。")
txt_input = st.text_area("テキスト貼付（任意）", height=160)

if (uploaded1 is None and not txt_input) and uploaded2 is None:
    st.info("①と②のうちどちらか片方でもPDFアップ、あるいはテキスト貼付を行ってください。")
    st.stop()

# Prepare current / prev line lists
cur_lines = []
prev_lines = []

if uploaded1:
    buf = BytesIO(uploaded1.read())
    cur_lines = extract_lines_from_pdf(buf)

if uploaded2:
    buf2 = BytesIO(uploaded2.read())
    prev_lines = extract_lines_from_pdf(buf2)

# If user pasted text, use it as current (if there's no uploaded1)
if txt_input and not uploaded1:
    # treat pasted text as "current"
    cur_lines = [l.strip() for l in txt_input.split("\n") if l.strip()]

# If we still have no current lines, error
if not cur_lines:
    st.error("解析対象の「今回請求分」が見つかりません。PDFまたはテキストを指定してください。")
    st.stop()

with st.spinner("解析中..."):
    df_current = parse_text_lines_to_records(cur_lines)
    df_prev = parse_text_lines_to_records(prev_lines) if prev_lines else pd.DataFrame(columns=["id","name","cycle","remarks","amount_val"])

# validate results
if df_current.empty:
    st.error("今回分から利用者レコードが検出されませんでした。")
    st.write("解析した行の先頭20行（確認用）:")
    st.write(cur_lines[:20])
    st.stop()

# dedupe by id
df_current = df_current.drop_duplicates(subset=["id"], keep="first").reset_index(drop=True)
df_prev = df_prev.drop_duplicates(subset=["id"], keep="first").reset_index(drop=True)

# merge and prepare display
merged = pd.merge(df_current, df_prev[["id","amount_val"]], on="id", how="left", suffixes=("_curr","_prev"))
merged["is_new"] = merged["amount_val_prev"].isna()
merged["is_diff"] = (~merged["is_new"]) & (merged["amount_val_curr"] != merged["amount_val_prev"])
merged["is_same"] = (~merged["is_new"]) & (merged["amount_val_curr"] == merged["amount_val_prev"])

def fmt_num(v):
    try:
        return f"{int(v):,}"
    except:
        return "0"

view = merged.copy()
view["今回請求額"] = view["amount_val_curr"].apply(fmt_num)
view["前回請求額"] = view["amount_val_prev"].apply(lambda x: fmt_num(x) if pd.notnull(x) else "該当なし")

display = view[["id","name","cycle","remarks","今回請求額","前回請求額","is_new","is_diff","is_same"]].copy()
display.columns = ["ID","利用者名","請求サイクル","備考","今回請求額","前回請求額","is_new","is_diff","is_same"]

# styling
def highlight_rows(row):
    styles = ['' for _ in range(len(row))]
    # base white
    for i in range(len(styles)):
        styles[i] = 'background-color: white; color: black;'
    if row['is_new']:
        styles[4] = 'background-color:#ffe6e6; color:red; font-weight:bold;'
    elif row['is_same']:
        styles[4] = styles[5] = 'color:#999;'
    elif row['is_diff']:
        styles[4] = 'background-color:#ffe6e6; color:red; font-weight:bold;'
        styles[5] = 'color:blue; font-weight:bold;'
    if '◆請◆' in str(row['備考']):
        for i in range(len(styles)):
            if 'background-color' in styles[i]:
                styles[i] = styles[i].replace('background-color: white', 'background-color: #ffffcc')
            else:
                styles[i] = 'background-color: #ffffcc; color: black;'
    return styles

st.markdown("### 判定結果")
st.caption("備考に『◆請◆』あり：黄色 / 新規・変更：赤背景 / 一致：金額グレー")

st.dataframe(display.style.apply(highlight_rows, axis=1), use_container_width=True, height=700)

st.download_button("結果をCSVでダウンロード", display.to_csv(index=False).encode("utf-8-sig"), "check_result.csv")
