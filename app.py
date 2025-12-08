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
    """金額文字列を数値に変換"""
    if not isinstance(x, str): return 0
    # 改行がある場合は1行目のみ評価
    if '\n' in x: x = x.split('\n')[0]
    
    s = x.replace(',', '').replace('円', '').replace('¥', '').replace(' ', '').strip()
    table = str.maketrans('０１２３４５６７８９', '0123456789')
    s = s.translate(table)
    try:
        # 数字以外の文字が混ざりすぎていないかチェック（ID誤認防止）
        # 純粋な数値パターンだけを探す
        match = re.search(r'^-?\d+$', s) # 完全一致に近い形のみ許可
        if not match:
            # カンマ区切りの数値を探す
            match = re.search(r'-?\d+', s)
        
        if match:
            return int(match.group())
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
                    if not any(row): continue
                    
                    # セルの中身（テキスト）を結合して取得
                    cells = [str(cell).strip() if cell is not None else "" for cell in row]
                    non_empty_cells = [c for c in cells if c]
                    
                    if not non_empty_cells: continue
                    
                    # サイクル文字の抽出
                    cycle_text = ""
                    for cell in non_empty_cells:
                        if re.search(r'\d+\s*(?:ヶ月|年)', cell):
                            cycle_text = cell
                            break

                    # ========================================================
                    # 【判定ロジック 1】 列数チェック
                    # 2列未満（ID列しかない等）の場合は、金額列なし＝備考とみなす
                    # ========================================================
                    if len(non_empty_cells) < 2:
                        amount_val = 0
                    else:
                        amount_str = non_empty_cells[-1] 
                        amount_val = clean_currency(amount_str)

                    # テキストブロック解析
                    key_text_block = non_empty_cells[0]
                    lines = key_text_block.split('\n')
                    
                    # ========================================================
                    # 【判定ロジック 2】 行の処理
                    # ========================================================
                    
                    # --- ケースA: 金額が0円（または列不足）の場合 ---
                    # この行は絶対に利用者ではない。備考確定。
                    if amount_val == 0:
                        if current_record:
                            for line in lines:
                                line = line.strip()
                                if is_ignore_line(line): continue
                                
                                if cycle_text and cycle_text in line:
                                    line = line.replace(cycle_text, "").strip()
                                if line:
                                    current_record["remarks"].append(line)
                        continue

                    # --- ケースB: 金額がある場合（利用者行の可能性が高い） ---
                    user_found_in_this_row = False
                    
                    for line in lines:
                        line = line.strip()
                        if is_ignore_line(line): continue
                        
                        # IDパターンチェック
                        match = re.match(r'^(\d{6,})(.*)', line)
                        
                        if match and '/' not in line:
                            user_id = match.group(1)
                            user_name = match.group(2).strip()
                            
                            # 【重要】NGキーワードチェック（ここを最強にしました）
                            # 本来の氏名に「様」や「（」は絶対に入らない。これがあれば100%備考。
                            # 「奥」「主」などは苗字にある可能性があるので「奥様」「主人」で判定
                            ng_keywords = [
                                "様", "奥様", "主人", "旦那", "回収", "集金", "亡", "同時", "義母", "義父", 
                                "（", "(", "→", "別", "居宅"
                            ]
                            
                            has_ng = any(kw in user_name for kw in ng_keywords)
                            
                            # NGワードがなく、かつこの行でまだ誰も登録していない場合
                            if not has_ng and not user_found_in_this_row:
                                # ★正規の利用者として登録★
                                if cycle_text and cycle_text in user_name:
                                    user_name = user_name.replace(cycle_text, "").strip()
                                
                                current_record = {
                                    "id": user_id,
                                    "name": user_name,
                                    "cycle": cycle_text, 
                                    "remarks": [],
                                    "amount_val": amount_val
                                }
                                data_list.append(current_record)
                                user_found_in_this_row = True
                            else:
                                # NGワード入り or 2人目のID は備考へ
                                if current_record:
                                    if cycle_text and cycle_text in line:
                                        line = line.replace(cycle_text, "").strip()
                                    current_record["remarks"].append(line)
                        
                        else:
                            # IDでない行はすべて備考へ
                            if current_record:
                                if cycle_text and cycle_text in line:
                                    line = line.replace(cycle_text, "").strip()
                                if line != cycle_text: 
                                    current_record["remarks"].append(line)
    
    # DataFrame化
    final_data = []
    for rec in data_list:
        final_data.append({
            "id": rec["id"],
            "name": rec["name"],
            "cycle": rec["cycle"],
            "remarks": " ".join(rec["remarks"]),
            "amount_val": rec["amount_val"]
        })
        
    return pd.DataFrame(final_data)

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
