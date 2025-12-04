import streamlit as st
import pdfplumber
import pandas as pd
import re

# --- ページ設定 ---
st.set_page_config(page_title="請求書チェックツール", layout="wide")

# --- 簡易認証機能 ---
def check_password():
    if "password_correct" not in st.session_state:
        st.session_state.password_correct = False
    if st.session_state.password_correct:
        return True
    
    st.title("🔒 ログイン")
    password = st.text_input("パスワードを入力してください", type="password")
    if st.button("ログイン"):
        # Secretsに設定したパスワードと照合
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

def clean_text(text):
    """改行や余計な空白を削除"""
    if text is None:
        return ""
    return str(text).replace('\n', '').strip()

def clean_currency(x):
    """金額文字列を数値に変換"""
    if not isinstance(x, str):
        return 0
    # カンマ、円、スペースを除去
    s = x.replace(',', '').replace('円', '').replace('¥', '').replace(' ', '').strip()
    # 全角数字を半角に変換
    table = str.maketrans('０１２３４５６７８９', '0123456789')
    s = s.translate(table)
    try:
        # 数値部分を抽出
        match = re.search(r'-?\d+', s)
        if match:
            return int(match.group())
    except:
        pass
    return 0

def extract_fixed_format(file):
    """
    固定フォーマット抽出（改良版）
    - 空欄の列を自動でスキップして、ID（数字6桁以上）を探します
    """
    data_list = []
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            # テキストベースで表を認識
            tables = page.extract_tables(table_settings={
                "vertical_strategy": "text", 
                "horizontal_strategy": "text",
                "snap_tolerance": 3
            })
            
            for table in tables:
                for row in table:
                    # 空行スキップ
                    if not any(row):
                        continue
                        
                    # 1. まず行の中身をクリーニング
                    cleaned_row = [clean_text(cell) for cell in row if cell is not None]
                    
                    # 2. 「完全に空っぽの列」を削除して詰める
                    cleaned_row = [c for c in cleaned_row if c != ""]
                    
                    # データが少なすぎる行はスキップ
                    if len(cleaned_row) < 2:
                        continue

                    # 3. 詰めた後の「先頭」をキー、「最後尾」を金額とする
                    key = cleaned_row[0]
                    amount_str = cleaned_row[-1]
                    
                    # === フィルター処理 ===
                    # キーの中に「数字6桁以上」が含まれていない行は無視する
                    if not re.search(r'\d{6,}', key):
                        continue
                    
                    # 日付(スラッシュ入り)がキーになってしまっている場合は除外
                    if '/' in key:
                        continue
                    # ==================================

                    # 金額変換
                    amount_val = clean_currency(amount_str)
                    
                    # リストに追加
                    data_list.append({
                        "key": key,
                        "amount_raw": amount_str, # 表示用の元の文字列
                        "amount_val": amount_val  # 計算用の数値
                    })

    return pd.DataFrame(data_list)

# ==========================================
# アプリ画面
# ==========================================

st.title('📄 レンタル伝票 差異チェックツール')
st.caption("利用者IDと名前の行のみを自動抽出して比較します")

col1, col2 = st.columns(2)
with col1:
    file_master = st.file_uploader("① 正しいデータ (Master)", type="pdf", key="m")
with col2:
    file_target = st.file_uploader("② 確認したいデータ (Target)", type="pdf", key="t")

if file_master and file_target:
    with st.spinner('比較中...'):
        # 1. データ抽出
        df_master = extract_fixed_format(file_master)
        df_target = extract_fixed_format(file_target)

        # エラーハンドリング
        if df_master.empty or df_target.empty:
            st.error("有効なデータが見つかりませんでした。")
        else:
            # 2. 重複排除
            df_master = df_master.drop_duplicates(subset=['key'])
            df_target = df_target.drop_duplicates(subset=['key'])
            
            # 3. ②(Target)をベースに、①(Master)を結合
            merged = pd.merge(
                df_target, 
                df_master[['key', 'amount_val']], 
                on='key', 
                how='left', 
                suffixes=('', '_master')
            )
            
            # 4. 判定ロジック
            merged['is_diff'] = (merged['amount_val'] != merged['amount_val_master']) & (merged['amount_val_master'].notna())
            merged['is_new'] = merged['amount_val_master'].isna()
            
            # 5. 表示用データの整形
            display_df = merged.copy()
            
            # 「正しい金額(①)」列を作る
            display_df['correct_val'] = display_df.apply(
                lambda row: f"{int(row['amount_val_master']):,}" if row['is_diff'] else "", axis=1
            )
            
            # 表示列の整理
            final_view = display_df[['key', 'amount_raw', 'correct_val', 'is_diff', 'is_new']].copy()
            final_view.columns = ['利用者名/ID', '請求額(②)', '正しい金額(①)', 'is_diff', 'is_new']

            # ==========================================
            # スタイリング
            # ==========================================
            def highlight_rows(row):
                styles = [''] * len(row)
                
                # ケース2: ①になくて②にある行
                if row['is_new']:
                    return ['background-color: #f0f0f0; color: #a0a0a0; text-decoration: line-through;'] * len(row)
                
                # ケース1: 金額不一致
                if row['is_diff']:
                    styles[1] = 'color: red; font-weight: bold; background-color: #ffe6e6;'
                    styles[2] = 'color: blue; font-weight: bold;'
                
                return styles

            st.markdown("### 判定結果")
            st.info("赤色：金額相違（右に正しい金額を表示） / グレー：①にデータ無し")
            
            # Pandas Styler適用
            styled_df = final_view.style.apply(highlight_rows, axis=1)
            
            # 【ここが修正点】表示したい列だけを指定して、右2列（フラグ）を強制的に隠す
            st.dataframe(
                styled_df,
                column_order=['利用者名/ID', '請求額(②)', '正しい金額(①)'], 
                use_container_width=True,
                height=800
            )
            
            # CSVダウンロード
            csv_data = final_view.drop(columns=['is_diff', 'is_new'])
            st.download_button(
                "結果をCSVでダウンロード",
                csv_data.to_csv(index=False).encode('utf-8-sig'),
                "check_result.csv"
            )
