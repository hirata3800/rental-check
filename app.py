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
    """改行をスペースに置換してトリム（改行を消さない）"""
    if text is None:
        return ""
    # 改行を消さずに残す（あとで分離するため）、ただし前後の空白は消す
    return str(text).strip()

def clean_currency(x):
    """金額文字列を数値に変換"""
    if not isinstance(x, str):
        return 0
    # 改行が含まれている場合、数値っぽいものを探す
    # カンマ、円、スペースを除去
    s = x.replace(',', '').replace('円', '').replace('¥', '').replace(' ', '').replace('\n', '')
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

def split_id_name(text):
    """IDと名前を分離する"""
    # 余計な空白を除去
    text = text.strip()
    match = re.match(r'^(\d{6,})\s*(.*)', text)
    if match:
        return match.group(1), match.group(2).strip()
    return text, ""

def extract_detailed_format(file):
    """
    ID、名前、備考(セル内改行 + 次の行)、金額を抽出する
    """
    data_list = []
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables(table_settings={
                "vertical_strategy": "text", 
                "horizontal_strategy": "text",
                "snap_tolerance": 3
            })
            
            for table in tables:
                i = 0
                while i < len(table):
                    row = table[i]
                    
                    # 空行スキップ
                    if not any(row):
                        i += 1
                        continue
                    
                    # 生のセルデータを取得（Noneは空文字に）
                    raw_cells = [str(cell) if cell is not None else "" for cell in row]
                    
                    # 完全に空の列を詰める処理
                    non_empty_cells = [c for c in raw_cells if c.strip() != ""]
                    
                    if len(non_empty_cells) < 2:
                        i += 1
                        continue

                    # キー情報の取得（一番左の列）
                    key_raw_text = non_empty_cells[0]
                    amount_str = non_empty_cells[-1]
                    
                    # === ID行の判定 ===
                    # セル内のどこかに「数字6桁以上」が含まれているか？
                    # 改行で区切られている可能性があるので、行ごとにチェック
                    lines = key_raw_text.split('\n')
                    
                    # IDが含まれる行を探す（通常は1行目）
                    id_line_index = -1
                    for idx, line in enumerate(lines):
                        if re.search(r'^\d{6,}', line.strip()) and '/' not in line:
                            id_line_index = idx
                            break
                    
                    if id_line_index != -1:
                        # === IDと名前の抽出 ===
                        target_line = lines[id_line_index]
                        user_id, user_name = split_id_name(target_line)
                        
                        # === 備考の抽出 ①（同じセル内の改行） ===
                        # ID行より後ろにある行はすべて「備考」とみなす
                        in_cell_remarks = []
                        if id_line_index + 1 < len(lines):
                            in_cell_remarks = lines[id_line_index+1:]
                        
                        remarks_list = [r.strip() for r in in_cell_remarks if r.strip()]
                        
                        amount_val = clean_currency(amount_str)
                        
                        # === 備考の抽出 ②（次の行を見る） ===
                        # 次のテーブル行を見て、ID行でなければ備考として追加
                        if i + 1 < len(table):
                            next_row = table[i+1]
                            # 次の行をきれいにする
                            next_row_clean = [str(c).strip() for c in next_row if c is not None and str(c).strip() != ""]
                            
                            if next_row_clean:
                                next_key = next_row_clean[0]
                                # 次の行の先頭がID（数字6桁）でなければ、それは備考行
                                # ただし日付などは除外しないといけないが、備考の一部かもしれないので含める
                                if not re.search(r'^\d{6,}', next_key):
                                    # 改行を除去して結合
                                    cleaned_next_text = " ".join
