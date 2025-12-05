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

def split_id_name(text):
    """IDと名前を分離する (例: '000123 田中' -> '000123', '田中')"""
    text = clean_text(text)
    match = re.match(r'^(\d{6,})\s*(.*)', text)
    if match:
        return match.group(1), match.group(2)
    return text, "" # 分離できない場合はIDにそのまま入れる

def extract_detailed_format(file):
    """
    ID、名前、備考(次の行)、金額を抽出する
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
                # 行インデックスを使ってループ（次の行を見るため）
                i = 0
                while i < len(table):
                    row = table[i]
                    
                    # 空行スキップ
                    if not any(row):
                        i += 1
                        continue
                        
                    cleaned_row = [clean_text(cell) for cell in row if cell is not None]
                    cleaned_row = [c for c in cleaned_row if c != ""]
                    
                    if len(cleaned_row) < 2:
                        i += 1
                        continue

                    # キー情報の取得
                    key_raw = cleaned_row[0]
                    amount_str = cleaned_row[-1]
                    
                    # === ID行の判定 ===
                    # 先頭が「数字6桁以上」の場合、これをメイン行とみなす
                    if re.search(r'^\d{6,}', key_raw) and '/' not in key_raw:
                        
                        # IDと名前に分割
                        user_id, user_name = split_id_name(key_raw)
                        amount_val = clean_currency(amount_str)
                        
                        # === 備考の取得（次の行を見る） ===
                        remarks = ""
                        if i + 1 < len(table):
                            next_row = table[i+1]
                            # 次の行をきれいにする
                            next_row_clean = [clean_text(c) for c in next_row if c is not None and clean_text(c) != ""]
                            
                            # 次の行が存在し、かつ「次の行が別のID行ではない」場合、それを備考とする
                            if next_row_clean:
                                next_key = next_row_clean[0]
                                if not re.search(r'^\d{6,}', next_key):
                                    # 備考として採用
                                    remarks = " ".join(next_row_clean)
                                    # 備考行は処理したので、ループを1つ飛ばすかどうか？
                                    # 通常は飛ばさなくて良い（次のループでID判定されてスキップされるため）が
                                    # 安全のため読み捨ててもよい。ここでは読み捨てないでロジックに任せる
                        
                        data_list.append({
                            "id": user_id,
                            "name": user_name,
                            "remarks": remarks,
                            "amount_val": amount_val
                        })
                    
                    i += 1

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
        # 1. データ抽出
        df_current = extract_detailed_format(file_current)
        df_prev = extract_detailed_format(file_prev)

        if df_current.empty or df_prev.empty:
            st.error("有効なデータが見つかりませんでした。")
        else:
            # 2. 重複排除
            df_current = df_current.drop_duplicates(subset=['id'])
            df_prev = df_prev.drop_duplicates(subset=['id'])
            
            # 3. 今回(①)を基準に、前回(②)を結合 (Left Join)
            merged = pd.merge(
                df_current, 
                df_prev[['id', 'amount_val']], 
                on='id', 
                how='left', 
                suffixes=('_curr', '_prev')
            )
            
            # 4. 判定ロジック
            # 前回データがない(NaN) -> 新規
            merged['is_new'] = merged['amount_val_prev'].isna()
            
            # 金額が違う (かつ新規ではない) -> 差異あり
            merged['is_diff'] = (~merged['is_new']) & (merged['amount_val_curr'] != merged['amount_val_prev'])
            
            # 金額が同じ -> 一致
            merged['is_same'] = (~merged['is_new']) & (merged['amount_val_curr'] == merged['amount_val_prev'])

            # 5. 表示用データの整形
            # 表示用に数値をカンマ区切り文字列に、Noneは空文字に
            def format_num(val):
                return f"{int(val):,}" if pd.notnull(val) else ""

            display_df = merged.copy()
            display_df['今回請求額'] = display_df['amount_val_curr'].apply(format_num)
            display_df['前回請求額'] = display_df['amount_val_prev'].apply(format_num)
            
            # 列の並び替え
            final_view = display_df[['id', 'name', 'remarks', '今回請求額', '前回請求額', 'is_new', 'is_diff', 'is_same']].copy()
            final_view.columns = ['ID', '利用者名', '備考', '今回請求額', '前回請求額', 'is_new', 'is_diff', 'is_same']

            # ==========================================
            # スタイリング (色の設定)
            # ==========================================
            def highlight_rows(row):
                styles = [''] * len(row)
                
                # ケース1: ①にあって②にない (新規) -> 行全体を薄黄色
                if row['is_new']:
                    return ['background-color: #ffffe0; color: black;'] * len(row)
                
                # ケース2: 比較結果が同じ -> 行全体をグレーアウト(文字色グレー)
                if row['is_same']:
                    return ['color: #d3d3d3;'] * len(row)

                # ケース3: 金額相違 -> 今回を赤、前回を青
                if row['is_diff']:
                    # ID, 名前, 備考は黒
                    styles[0] = 'color: black;'
                    styles[1] = 'color: black;'
                    styles[2] = 'color: black;'
                    # 今回請求額(Col 3) -> 赤
                    styles[3] = 'color: red; font-weight: bold; background-color: #ffe6e6;'
                    # 前回請求額(Col 4) -> 青
                    styles[4] = 'color: blue; font-weight: bold;'
                
                return styles

            st.markdown("### 判定結果")
            st.info("背景黄色：今回のみ(新規) / 文字グレー：前回と一致 / 赤青：金額変更")
            
            # Pandas Styler適用
            styled_df = final_view.style.apply(highlight_rows, axis=1)
            
            # フラグ列を非表示
            styled_df = styled_df.hide(axis="columns", subset=['is_new', 'is_diff', 'is_same'])

            # データフレーム表示
            # Streamlitのdataframeはデフォルトでセル選択→Ctrl+Cが可能です
            st.dataframe(
                styled_df,
                use_container_width=True,
                height=800,
                column_config={
                    "ID": st.column_config.TextColumn("ID"), # 数字として扱わない(カンマなし)
                }
            )
            
            # CSVダウンロード
            csv_data = final_view.drop(columns=['is_new', 'is_diff', 'is_same'])
            st.download_button(
                "結果をCSVでダウンロード",
                csv_data.to_csv(index=False).encode('utf-8-sig'),
                "check_result.csv"
            )
