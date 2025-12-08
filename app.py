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
        pointer-events: none !important;
        cursor: default !important;
    }
    .block-container {
        padding-top: 1rem !important;
    }
    </style>
""", unsafe_allow_html=True)

# --- 簡易認証機能 ---
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

# ==========================================
# データ処理機能
# ==========================================

def clean_currency(x):
    if not isinstance(x, str): return 0
    s = x.replace(',', '').replace('円', '').replace('¥', '').replace(' ', '').replace('\n', '')
    table = str.maketrans('０１２３４５６７８９', '0123456789')
    s = s.translate(table)
    try:
        match = re.search(r'-?\d+', s)
        if match: return int(match.group())
    except: pass
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

def extract_detailed_format(file):
    # 1. まずは全てのID行候補を一旦抽出する
    raw_records = []
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
                    if not any(row): continue
                    
                    cells = [str(cell) if cell is not None else "" for cell in row]
                    non_empty_cells = [c for c in cells if c.strip() != ""]
                    if not non_empty_cells: continue
                        
                    key_text_block = non_empty_cells[0]
                    amount_str = non_empty_cells[-1] if len(non_empty_cells) > 1 else ""
                    amount_val = clean_currency(amount_str)

                    cycle_text = ""
                    for cell in non_empty_cells:
                        match = re.search(r'(\d+\s*(?:ヶ月|年))', cell)
                        if match:
                            cycle_text = match.group(1) 
                            break

                    lines = key_text_block.split('\n')
                    for line in lines:
                        line = line.strip()
                        if is_ignore_line(line): continue
                        
                        # 数字6桁以上で始まっていれば、一旦レコード候補とする
                        match = re.match(r'^(\d{6,})(.*)', line)
                        
                        if match and '/' not in line:
                            user_id = match.group(1)
                            user_name = match.group(2).strip()
                            
                            # 名前からサイクル文字を削除
                            if cycle_text and cycle_text in user_name:
                                user_name = user_name.replace(cycle_text, "").strip()

                            current_record = {
                                "id": user_id,
                                "name": user_name,
                                "cycle": cycle_text, 
                                "remarks": [],
                                "amount_val": amount_val
                            }
                            raw_records.append(current_record)
                        else:
                            # ID行でないなら備考
                            if current_record is not None:
                                if not current_record["cycle"] and cycle_text:
                                    current_record["cycle"] = cycle_text
                                
                                if line != cycle_text:
                                    if cycle_text and cycle_text in line:
                                        line = line.replace(cycle_text, "").strip()
                                    if line:
                                        current_record["remarks"].append(line)

    # 2. 【重要】抽出後のデータをチェックし、不適切なID行を前の人の備考にマージする
    final_records = []
    
    if raw_records:
        # 最初の1件目は無条件に追加
        final_records.append(raw_records[0])
        
        for i in range(1, len(raw_records)):
            current = raw_records[i]
            prev = final_records[-1]
            
            # 名前に「本来の名前に含まれないはずのキーワード」があるかチェック
            # これがあれば、ID行ではなく「備考」とみなして前の人にくっつける
            ignore_keywords = [
                "様の", "の奥様", "のご主人", "の旦那", "家族", "娘", "息子", "親戚", 
                "回収", "集金", "亡", "同時", "義母", "義父", "奥さん",
                "（", "→", "別", "居宅", "ケアマネ"
            ]
            
            # 名前の中にキーワードが含まれているか？
            is_fake_user = any(kw in current['name'] for kw in ignore_keywords)
            
            # または名前が空っぽ（IDだけ）の場合も怪しい
            if not current['name']:
                is_fake_user = True

            if is_fake_user:
                # これは備考だ！前の人の備考に追加する
                # 元のテキストを復元して追加 (例: "0100... 藤吉様の奥様")
                merged_text = f"{current['id']} {current['name']}"
                if current['remarks']:
                    merged_text += " " + " ".join(current['remarks'])
                
                prev['remarks'].append(merged_text)
                
                # もしこの行に金額が入っていたらどうするか？通常備考行には金額はないはずだが、
                # もしあれば前の人に足すなどの処理が必要かもしれないが、一旦無視する
            else:
                # 正しい利用者なのでリストに追加
                final_records.append(current)

    # 3. DataFrame化
    data_list = []
    for rec in final_records:
        data_list.append({
            "id": rec["id"],
            "name": rec["name"],
            "cycle": rec["cycle"],
            "remarks": " ".join(rec["remarks"]),
            "amount_val": rec["amount_val"]
        })
        
    return pd.DataFrame(data_list)

# ==========================================
# アプリ画面
# ==========================================

st.title('📄 レンタル伝票 差異チェックツール')
st.caption("①今回分を基準に、②前回分と比較します。")

col1, col2 = st.columns(2)
with col1:
    file_current = st.file_uploader("① 今回請求分 (Current)", type="pdf", key="m")
with col2:
    file_prev = st.file_uploader("② 前回請求分 (Previous)", type="pdf", key="t")

if file_current and file_prev:
    with st.spinner('比較中...'):
        df_current = extract_detailed_format(file_current)
        df_prev = extract_detailed_format(file_prev)

        if df_current.empty or df_prev.empty:
            st.error("有効なデータが見つかりませんでした。")
        else:
            df_current = df_current.drop_duplicates(subset=['id'])
            df_prev = df_prev.drop_duplicates(subset=['id'])
            
            merged = pd.merge(
                df_current, 
                df_prev[['id', 'amount_val']], 
                on='id', 
                how='left', 
                suffixes=('_curr', '_prev')
            )
            
            # フラグ設定
            merged['is_new'] = merged['amount_val_prev'].isna()
            merged['is_diff'] = (~merged['is_new']) & (merged['amount_val_curr'] != merged['amount_val_prev'])
            merged['is_same'] = (~merged['is_new']) & (merged['amount_val_curr'] == merged['amount_val_prev'])

            # 表示用フォーマット
            def format_curr(val):
                return f"{int(val):,}" if pd.notnull(val) else "0"
            def format_prev(val):
                return f"{int(val):,}" if pd.notnull(val) else "該当なし"

            display_df = merged.copy()
            display_df['今回請求額'] = display_df['amount_val_curr'].apply(format_curr)
            display_df['前回請求額'] = display_df['amount_val_prev'].apply(format_prev)
            
            final_view = display_df[['id', 'name', 'cycle', 'remarks', '今回請求額', '前回請求額', 'is_new', 'is_diff', 'is_same']].copy()
            final_view.columns = ['ID', '利用者名', '請求サイクル', '備考', '今回請求額', '前回請求額', 'is_new', 'is_diff', 'is_same']

            # ==========================================
            # スタイリング
            # ==========================================
            def highlight_rows(row):
                bg_color = 'white'
                text_color = 'black'
                
                if '◆請◆' in str(row['備考']):
                    bg_color = '#ffffcc'
                
                base_style = f'background-color: {bg_color}; color: {text_color};'
                styles = [base_style] * len(row)
                
                if row['is_new']:
                    styles[4] = 'color: red; font-weight: bold; background-color: #ffe6e6;'
                elif row['is_same']:
                    grey_style = f'color: #a0a0a0; background-color: {bg_color};'
                    styles[4] = grey_style
                    styles[5] = grey_style
                elif row['is_diff']:
                    styles[4] = 'color: red; font-weight: bold; background-color: #ffe6e6;'
                    styles[5] = f'color: blue; font-weight: bold; background-color: {bg_color};'
                return styles

            st.markdown("### 判定結果")
            st.caption("備考に「◆請◆」あり：黄色 / 新規・変更：赤背景 / 一致：金額グレー")
            
            styled_df = final_view.style.apply(highlight_rows, axis=1)

            st.dataframe(
                styled_df,
                use_container_width=True,
                height=800,
                column_config={
                    "ID": st.column_config.TextColumn("ID"),
                },
                column_order=['ID', '利用者名', '請求サイクル', '備考', '今回請求額', '前回請求額']
            )
            
            st.download_button(
                "結果をCSVでダウンロード (全項目)",
                final_view.to_csv(index=False).encode('utf-8-sig'),
                "check_result.csv"
            )
