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

def extract_detailed_format(file):
    # 1. まずは「IDっぽい行」を全て候補として抽出する
    raw_candidates = []
    
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
                    
                    # 行内の有効なセルを取得
                    cells = [str(cell) if cell is not None else "" for cell in row]
                    non_empty_cells = [c for c in cells if c.strip() != ""]
                    if not non_empty_cells: continue
                        
                    key_text_block = non_empty_cells[0]
                    amount_str = non_empty_cells[-1] if len(non_empty_cells) > 1 else ""
                    amount_val = clean_currency(amount_str)

                    # サイクル文字の抽出
                    cycle_text = ""
                    for cell in non_empty_cells:
                        match = re.search(r'(\d+\s*(?:ヶ月|年))', cell)
                        if match:
                            cycle_text = match.group(1) 
                            break

                    lines = key_text_block.split('\n')
                    for line in lines:
                        line = line.strip()
                        if not line: continue
                        
                        # --- 候補データの作成 ---
                        # 数字6桁以上で始まれば、一旦「候補」とする
                        match = re.match(r'^(\d{6,})(.*)', line)
                        
                        if match and '/' not in line and "ページ" not in line:
                            user_id = match.group(1)
                            raw_name = match.group(2).strip()
                            
                            # 名前からサイクル文字を削除
                            if cycle_text and cycle_text in raw_name:
                                raw_name = raw_name.replace(cycle_text, "").strip()

                            raw_candidates.append({
                                "type": "user_candidate",
                                "id": user_id,
                                "name": raw_name,
                                "cycle": cycle_text,
                                "amount": amount_val,
                                "original_line": line
                            })
                        else:
                            # IDでない行は「テキスト行」として保持
                            if line != cycle_text and "ページ" not in line:
                                raw_candidates.append({
                                    "type": "text",
                                    "content": line,
                                    "cycle": cycle_text # サイクルがある行の可能性もある
                                })

    # 2. 【重要】後処理フィルター：候補リストを精査して、本当の利用者と備考を振り分ける
    final_records = []
    
    # 判定用NGキーワード（名前にこれらが含まれていたら、それは利用者ではない）
    ng_keywords = [
        "様", "奥", "主", "夫", "妻", "娘", "息", "族", "親", # 続柄・敬称
        "亡", "集", "回", "同", "→", "(", "（", "【", "「", "介", "施" # 記号・動作
    ]

    for item in raw_candidates:
        if item["type"] == "user_candidate":
            name_check = item["name"]
            
            # 判定：名前にNGワードが含まれているか？
            is_fake_user = any(kw in name_check for kw in ng_keywords)
            
            # 判定：名前が極端に短い（1文字以下）または無い場合も怪しい
            if len(name_check) <= 1: 
                # ただし、本当に1文字の苗字の人もいるかもしれないが、通常スペースがあるので
                # ここでは安全のため弾かないでおく、またはスペース無しなら弾く
                pass

            if is_fake_user:
                # 偽ユーザー（備考欄の参照ID）だった場合
                # 直前の正しいユーザーの備考に追加する
                if final_records:
                    prev = final_records[-1]
                    prev["remarks"].append(item["original_line"])
            else:
                # 正しいユーザーとみなす
                new_record = {
                    "id": item["id"],
                    "name": item["name"],
                    "cycle": item["cycle"],
                    "remarks": [],
                    "amount_val": item["amount"]
                }
                final_records.append(new_record)
        
        elif item["type"] == "text":
            # 通常のテキスト行（備考）
            if final_records:
                prev = final_records[-1]
                # サイクル文字が混ざっていたら除去
                text = item["content"]
                if item["cycle"] and item["cycle"] in text:
                    text = text.replace(item["cycle"], "").strip()
                
                if text:
                    prev["remarks"].append(text)
                
                # もしこのテキスト行でサイクルが見つかっていれば補完
                if not prev["cycle"] and item["cycle"]:
                    prev["cycle"] = item["cycle"]

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
            
            merged['is_new'] = merged['amount_val_prev'].isna()
            merged['is_diff'] = (~merged['is_new']) & (merged['amount_val_curr'] != merged['amount_val_prev'])
            merged['is_same'] = (~merged['is_new']) & (merged['amount_val_curr'] == merged['amount_val_prev'])

            def format_curr(val):
                return f"{int(val):,}" if pd.notnull(val) else "0"
            def format_prev(val):
                return f"{int(val):,}" if pd.notnull(val) else "該当なし"

            display_df = merged.copy()
            display_df['今回請求額'] = display_df['amount_val_curr'].apply(format_curr)
            display_df['前回請求額'] = display_df['amount_val_prev'].apply(format_prev)
            
            final_view = display_df[['id', 'name', 'cycle', 'remarks', '今回請求額', '前回請求額', 'is_new', 'is_diff', 'is_same']].copy()
            final_view.columns = ['ID', '利用者名', '請求サイクル', '備考', '今回請求額', '前回請求額', 'is_new', 'is_diff', 'is_same']

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
