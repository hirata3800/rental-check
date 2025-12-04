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
    固定フォーマット抽出
    - 先頭が「6桁以上の数字」で始まる行だけを抽出します
    """
    data_list = []
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            # 罫線がない場合も考慮してテキストベースで表を認識
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
                        
                    # データのクリーニング
                    cleaned_row = [clean_text(cell) for cell in row if cell is not None]
                    
                    # データが少なすぎる行はスキップ
                    if len(cleaned_row) < 2:
                        continue

                    # 1列目をキー、最後尾を金額とする
                    key = cleaned_row[0]
                    amount_str = cleaned_row[-1]
                    
                    # === フィルター処理 (ここが重要) ===
                    # キーが「数字6桁以上」で始まっていない行はゴミとみなして捨てる
                    # 例: "0000011158 黒崎誠" -> OK
                    # 例: "ページ 1" -> NG
                    # 例: "備考..." -> NG
                    if not re.match(r'^\d{6,}', key):
                        continue
                    
                    # 日付(スラッシュ入り)が先頭に来ている場合も除外
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
st.caption("IDと名前がある行のみを自動抽出して比較します")

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

        if df_master.empty or
