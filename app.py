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
    固定フォーマット前提の抽出処理
    - 0列目: 利用者名/ID (Key)
    - 最後の列: 請求額 (Value)
    として扱います。
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
                    
                    # データが少なすぎる行（ヘッダーの断片など）はスキップ
                    if len(cleaned_row) < 2:
                        continue

                    # 【重要】固定フォーマットルール
                    # 1列目(index 0)をキー、最後尾(index -1)を金額とする
                    key = cleaned_row[0]
                    amount_str = cleaned_row[-1]
                    
                    # キーが「利用者名」などのヘッダー行っぽい場合はスキップ
                    if "利用者" in key or "請求額" in key or "金額" in amount_str:
                        continue
                        
                    # 金額が数値として読めるかチェック（読めないならゴミ行の可能性）
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
st.caption("左側の「>」を押すとメニューが隠れて広く使えます")

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

        if df_master.empty or df_target.empty:
            st.error("データが読み取れませんでした。PDFの形式を確認してください。")
        else:
            # 2. 重複排除（同じ人が複数行あるとエラーになるため、最後の行を採用などの処理）
            # ここではシンプルにそのままマージしますが、キー重複がある場合は注意
            df_master = df_master.drop_duplicates(subset=['key'])
            
            # 3. ②(Target)をベースに、①(Master)を結合
            merged = pd.merge(
                df_target, 
                df_master[['key', 'amount_val']], 
                on='key', 
                how='left', 
                suffixes=('', '_master')
            )
            
            # 4. 判定ロジック
            # 差異フラグ: ①にあるけど金額が違う
            merged['is_diff'] = (merged['amount_val'] != merged['amount_val_master']) & (merged['amount_val_master'].notna())
            # 未登録フラグ: ①に存在しない
            merged['is_new'] = merged['amount_val_master'].isna()
            
            # 5. 表示用データの整形
            display_df = merged.copy()
            
            # 「正しい金額(①)」列を作る（差異がある時だけ表示、それ以外は空欄）
            display_df['correct_val'] = display_df.apply(
                lambda row: f"{int(row['amount_val_master']):,}" if row['is_diff'] else "", axis=1
            )
            
            # 表示列の整理
            final_view = display_df[['key', 'amount_raw', 'correct_val', 'is_diff', 'is_new']].copy()
            final_view.columns = ['利用者名/ID', '請求額(②)', '正しい金額(①)', 'is_diff', 'is_new']

            # ==========================================
            # スタイリング（ここが色の設定）
            # ==========================================
            def highlight_rows(row):
                styles = [''] * len(row)
                
                # ケース2: ①になくて②にある行（全体グレー）
                if row['is_new']:
                    # 薄いグレーの背景 + 文字色を薄く + 取り消し線
                    return ['background-color: #f0f0f0; color: #a0a0a0; text-decoration: line-through;'] * len(row)
                
                # ケース1: 金額不一致（請求額を赤く、正しい金額を表示）
                if row['is_diff']:
                    # 請求額(②)の列は1番目（0始まり）
                    styles[1] = 'color: red; font-weight: bold; background-color: #ffe6e6;'
                    # 正しい金額(①)の列は2番目
                    styles[2] = 'color: blue; font-weight: bold;'
                
                return styles

            # フラグ列は隠してスタイル適用
            st.markdown("### 判定結果")
            st.info("赤色：金額相違（右に正しい金額を表示） / グレー：①にデータ無し")
            
            # Pandas Stylerを使って表示
            styled_df = final_view.style.apply(highlight_rows, axis=1)
            
            # 隠したい列（フラグ用）を非表示設定
            styled_df = styled_df.hide(axis="columns", subset=['is_diff', 'is_new'])

            st.dataframe(
                styled_df,
                use_container_width=True,
                height=800 # 表の高さを固定（スクロールで見やすく）
            )
            
            # CSVダウンロード（フラグ列を除いて出力）
            csv_data = final_view.drop(columns=['is_diff', 'is_new'])
            st.download_button(
                "結果をCSVでダウンロード",
                csv_data.to_csv(index=False).encode('utf-8-sig'),
                "check_result.csv"
            )
