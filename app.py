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
    if '\n' in x: x = x.split('\n')[0]
    
    s = x.replace(',', '').replace('円', '').replace('¥', '').replace(' ', '').strip()
    
    # 日付（スラッシュ入り）や年号（2025など）の誤認防止
    if '/' in s or '202' in s:
        return 0

    table = str.maketrans('０１２３４５６７８９', '0123456789')
    s = s.translate(table)
    try:
        # 純粋な数値のみ抽出
        match = re.search(r'-?\d+', s)
        if match: 
            val = int(match.group())
            # IDを金額と誤認しないよう、異常に桁が多い/少ない数字は無視する安全策
            # (請求額が100万円を超えることは稀、IDは10桁なので10億以上になる)
            if abs(val) > 1000000: 
                return 0
            return val
    except: pass
    return 0

def extract_detailed_format(file):
    data_list = []
    
    # NGワードリスト（これらが名前に含まれていたら絶対に利用者ではない）
    ng_keywords = [
        "様", "奥", "主", "夫", "妻", "娘", "息", "族", "親", 
        "回収", "集金", "亡", "同時", "義", "（", "(", "→", "別", "居宅",
        "ケアマネ", "入院", "入所", "死亡", "逝去"
    ]
    
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            # 抽出設定
            tables = page.extract_tables(table_settings={
                "vertical_strategy": "text", 
                "horizontal_strategy": "text",
                "snap_tolerance": 3
            })
            
            for table in tables:
                for row in table:
                    # 空行スキップ
                    if not any(row): continue
                    
                    # データを正規化（Noneを空文字に、空白除去）
                    row = [str(cell).strip() if cell is not None else "" for cell in row]
                    
                    # 少なくともIDと名前がある行以外は無視
                    non_empty = [c for c in row if c]
                    if len(non_empty) < 2:
                        continue

                    # --- 列の特定 ---
                    # 通常、IDは0番目、名前は1番目、金額は最後尾にあることが多い
                    # ここでは安全のため、行の中から「IDっぽいもの」「金額っぽいもの」を探すアプローチをとる
                    
                    user_id = ""
                    user_name = ""
                    amount_val = 0
                    cycle_text = ""
                    
                    # 1. IDと名前の特定
                    # 先頭のセルがID+名前になっているパターンが多い
                    first_cell = non_empty[0]
                    match = re.match(r'^(\d{6,})\s+(.*)', first_cell)
                    if match:
                        user_id = match.group(1)
                        user_name = match.group(2).strip()
                    else:
                        # セルが分かれている場合
                        if re.match(r'^\d{6,}$', row[0]):
                            user_id = row[0]
                            user_name = row[1] if len(row) > 1 else ""

                    # 2. 金額の特定（後ろから探す）
                    for cell in reversed(non_empty):
                        val = clean_currency(cell)
                        if val != 0:
                            amount_val = val
                            break
                    
                    # 3. サイクル文字の特定
                    for cell in non_empty:
                        if re.search(r'\d+\s*(?:ヶ月|年)', cell):
                            cycle_text = cell
                            break

                    # ---------------------------------------------------
                    # ★ 判定ロジック（ここが重要）★
                    # ---------------------------------------------------
                    
                    # 条件1: IDが取れていない、または名前がないならNG
                    if not user_id or not user_name:
                        continue

                    # 条件2: 金額が0円ならNG（備考とみなす）
                    if amount_val == 0:
                        continue

                    # 条件3: 【復活】名前にNGワードが含まれていたらNG
                    is_ng_name = any(kw in user_name for kw in ng_keywords)
                    if is_ng_name:
                        continue
                    
                    # 条件4: 名前にサイクル文字が含まれていたら除去
                    if cycle_text and cycle_text in user_name:
                        user_name = user_name.replace(cycle_text, "").strip()

                    # すべてクリアしたら登録
                    data_list.append({
                        "id": user_id,
                        "name": user_name,
                        "cycle": cycle_text,
                        "remarks": "", # 備考の結合処理は複雑になるため、まずはID抽出を優先
                        "amount_val": amount_val
                    })

    # DataFrame化
    df = pd.DataFrame(data_list)
    
    # 備考行の結合処理（簡易版：同じIDが連続していたらマージする処理は今回は省略し、
    # 確実なID抽出のみに特化させます。もし備考の結合が必要なら別途追加します）
    
    return df

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
            # 重複排除（同じIDが複数回出る場合、最初の1つを採用）
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
            
            # 備考列は今回空にしていますが、必要であれば抽出ロジックを追加します
            display_df['備考'] = "" 
            
            final_view = display_df[['id', 'name', 'cycle', '備考', '今回請求額', '前回請求額', 'is_new', 'is_diff', 'is_same']].copy()
            final_view.columns = ['ID', '利用者名', '請求サイクル', '備考', '今回請求額', '前回請求額', 'is_new', 'is_diff', 'is_same']

            def highlight_rows(row):
                bg_color = 'white'
                text_color = 'black'
                
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
            st.caption("新規・変更：赤背景 / 一致：金額グレー")
            
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
