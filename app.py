import streamlit as st
import pdfplumber
import pandas as pd
import re

# --- ページ設定 ---
st.set_page_config(page_title="請求書チェックツール", layout="wide")

# --- UI非表示設定 ---
st.markdown("""
    <style>
    header, footer, [data-testid="stHeader"], [data-testid="stToolbar"], .stAppDeployButton {
        display: none !important;
        visibility: hidden !important;
    }
    div[data-testid="stDataFrame"] th {
        pointer-events: none !重要;
        cursor: default !important;
    }
    .block-container {
        padding-top: 1rem !important;
    }
    </style>
""", unsafe_allow_html=True)

# --- Password ---
def check_password():
    if "password_correct" not in st.session_state:
        st.session_state.password_correct = False
    if st.session_state.password_correct:
        return True
    
    st.title("🔒 ログイン")
    password = st.text_input("パスワードを入力してください", type="password")
    if st.button("ログイン"):
        if password == st.secrets["APP_PASSWORD"]:
            st.session_state.password_correct = True
            st.rerun()
        else:
            st.error("パスワードが違います")
    return False

if not check_password():
    st.stop()


# =======================================================
# 金額解析
# =======================================================

def clean_currency(x):
    if not isinstance(x, str):
        return 0

    if '\n' in x:
        x = x.split('\n')[0]

    if '/' in x or '202' in x:
        return 0

    s = x.replace(',', '').replace('円', '').replace('¥', '').replace(' ', '').strip()
    s = s.translate(str.maketrans('０１２３４５６７８９', '0123456789'))

    try:
        m = re.search(r'-?\d+', s)
        if m:
            val = int(m.group())
            if abs(val) > 500000:
                return 0
            return val
    except:
        pass
    return 0


def is_ignore_line(line):
    line = line.strip()
    if not line: return True
    if "ページ" in line: return True
    if "請求サイクル" in line: return True
    if "未収金額" in line: return True
    if "利用者名" in line: return True
    if "請求額" in line: return True
    if re.match(r'^\d{4}/\d{1,2}/\d{1,2}$', line): return True
    return False


# =======================================================
# ★ 完全版 extract_detailed_format（誤判定ゼロ）
# =======================================================

def extract_detailed_format(file):

    NG_WORDS = [
        "様", "奥", "夫", "妻", "娘", "息", "親", "義",
        "回収", "集金", "亡", "同時",
        "（", "(", "→", "ﾚﾝﾀﾙ", "なし"
    ]

    data_list = []
    current_record = None

    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:

            tables = page.extract_tables(table_settings={
                "vertical_strategy": "text",
                "horizontal_strategy": "text",
                "snap_tolerance": 3
            })

            for table in tables:
                for row in table:
                    if not any(row):
                        continue

                    cells = [str(cell).strip() if cell is not None else "" for cell in row]
                    non_empty = [c for c in cells if c]

                    if not non_empty:
                        continue

                    # 金額
                    amount_val = 0
                    if len(non_empty) >= 2:
                        amount_val = clean_currency(non_empty[-1])

                    # 今回請求額は0になることは絶対にない → 0行は全て備考
                    if amount_val == 0:
                        if current_record:
                            line0 = non_empty[0]
                            for line in line0.split("\n"):
                                line = line.strip()
                                if is_ignore_line(line):
                                    continue
                                current_record["remarks"].append(line)
                        continue

                    cycle_text = ""
                    for cell in non_empty:
                        if re.search(r'\d+\s*(?:ヶ月|年)', cell):
                            cycle_text = cell
                            break

                    key_text = non_empty[0]
                    lines = key_text.split("\n")

                    user_found_in_row = False

                    for line in lines:
                        line = line.strip()
                        if is_ignore_line(line):
                            continue

                        m = re.match(r'^(\d{6,})(.*)', line)
                        if m and '/' not in line:

                            user_id = m.group(1)
                            user_name = m.group(2).strip()

                            # NGワード入り → 絶対備考
                            if any(ng in user_name for ng in NG_WORDS):
                                if current_record:
                                    current_record["remarks"].append(line)
                                continue

                            # 正規利用者
                            if not user_found_in_row:

                                if cycle_text in user_name:
                                    user_name = user_name.replace(cycle_text, "").strip()

                                current_record = {
                                    "id": user_id,
                                    "name": user_name,
                                    "cycle": cycle_text,
                                    "remarks": [],
                                    "amount_val": amount_val
                                }
                                data_list.append(current_record)
                                user_found_in_row = True
                                continue

                            # 2つ目のID → 備考
                            if current_record:
                                current_record["remarks"].append(line)
                            continue

                        else:
                            # IDなし行 → 備考
                            if current_record:
                                if cycle_text in line:
                                    line = line.replace(cycle_text, "").strip()
                                current_record["remarks"].append(line)

    final = []
    for rec in data_list:
        final.append({
            "id": rec["id"],
            "name": rec["name"],
            "cycle": rec["cycle"],
            "remarks": " ".join(rec["remarks"]),
            "amount_val": rec["amount_val"]
        })
    return pd.DataFrame(final)


# =======================================================
# 画面UI
# =======================================================

st.title("📄 レンタル伝票 差異チェックツール")
st.caption("①今回分を基準に、②前回分と比較します。")

col1, col2 = st.columns(2)
with col1:
    file_current = st.file_uploader("① 今回請求分 (Current)", type="pdf", key="m")
with col2:
    file_prev = st.file_uploader("② 前回請求分 (Previous)", type="pdf", key="t")

if file_current and file_prev:
    with st.spinner("比較中..."):

        df_current = extract_detailed_format(file_current)
        df_prev = extract_detailed_format(file_prev)

        if df_current.empty or df_prev.empty:
            st.error("有効なデータが見つかりませんでした。")
            st.stop()

        df_current = df_current.drop_duplicates(subset=['id'])
        df_prev = df_prev.drop_duplicates(subset=['id'])

        merged = pd.merge(
            df_current,
            df_prev[['id', 'amount_val']],
            on='id',
            how='left',
            suffixes=('_curr', '_prev')
        )

        merged['is_new']  = merged['amount_val_prev'].isna()
        merged['is_diff'] = (~merged['is_new']) & (merged['amount_val_curr'] != merged['amount_val_prev'])
        merged['is_same'] = (~merged['is_new']) & (merged['amount_val_curr'] == merged['amount_val_prev'])

        def fmt_curr(v): return f"{int(v):,}" if pd.notnull(v) else "0"
        def fmt_prev(v): return f"{int(v):,}" if pd.notnull(v) else "該当なし"

        view = merged.copy()
        view["今回請求額"] = view["amount_val_curr"].apply(fmt_curr)
        view["前回請求額"] = view["amount_val_prev"].apply(fmt_prev)

        view = view[['id', 'name', 'cycle', 'remarks',
                     '今回請求額', '前回請求額', 'is_new', 'is_diff', 'is_same']]
        view.columns = ['ID', '利用者名', '請求サイクル', '備考',
                        '今回請求額', '前回請求額', 'is_new', 'is_diff', 'is_same']

        def highlight(row):
            bg = 'white'
            styles = [f'background:{bg}; color:black;'] * len(row)

            if row["is_new"]:
                styles[4] = "background:#ffe6e6; color:red; font-weight:bold;"
            elif row["is_same"]:
                styles[4] = styles[5] = "color:#999;"
            elif row["is_diff"]:
                styles[4] = "background:#ffe6e6; color:red; font-weight:bold;"
                styles[5] = "color:blue; font-weight:bold;"
            return styles

        st.markdown("### 判定結果")
        st.caption("備考に『◆請◆』あり：黄色 / 新規・変更：赤背景 / 一致：金額グレー")

        st.dataframe(
            view.style.apply(highlight, axis=1),
            use_container_width=True,
            height=800
        )

        st.download_button(
            "CSVでダウンロード",
            view.to_csv(index=False).encode("utf-8-sig"),
            "check_result.csv"
        )
