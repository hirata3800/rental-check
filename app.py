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
    """テキストのクリーニング"""
    if text is None:
        return ""
    return str(text).strip()

def clean_currency(x):
    """金額文字列を数値に変換"""
    if not isinstance(x, str):
        return 0
    # 改行や記号を除去
    s = x.replace(',', '').replace('円', '').replace('¥', '').replace(' ', '').replace('\n', '')
    table = str.maketrans('０１２３４５６７８９', '0123456789')
    s = s.translate(table)
    try:
        match = re.search(r'-?\d+', s)
        if match:
            return int(match.group())
    except:
        pass
    return 0

def split_id_name(text):
    """IDと名前を分離する"""
    text = text.strip()
    # "123456 名前" の形式を想定
    match = re.match(r'^(\d{6,})\s*(.*)', text)
    if match:
        return match.group(1), match.group(2).strip()
    # マッチしない場合はそのまま返す
    return "", text

def is_ignore_line(line):
    """ヘッダーやページ番号など、無視すべき行か判定"""
    line = line.strip()
    if not line: return True
    if "ページ" in line: return True
    if "請求サイクル" in line: return True
    if "未収金額" in line: return True
    if "利用者名" in line: return True
    if "請求額" in line: return True
    # 日付だけの行も無視 (例: 2025/11/25)
    if re.match(r'^\d{4}/\d{1,2}/\d{1,2}$', line): return True
    return False

def extract_detailed_format(file):
    """
    ストリーム読み取り方式：
    行ごとではなく、テキストの流れを見て「IDが出たら新規」「それ以外は直前の人の備考」と判定
    """
    extracted_records = []
    current_record = None # 現在処理中の人
    
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
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
                    
                    # セルデータを取得（None対策）
                    cells = [str(cell) if cell is not None else "" for cell in row]
                    
                    # 1列目（ID/名前/備考が入る列）と、最後尾（金額列）を取得
                    # 空のセルを除外して詰める
                    non_empty_cells = [c for c in cells if c.strip() != ""]
                    if not non_empty_cells:
                        continue
                        
                    key_text_block = non_empty_cells[0] # ここにIDや名前、備考が入っている
                    amount_str = non_empty_cells[-1] if len(non_empty_cells) > 1 else ""
                    
                    # 金額の取得（ID行の金額を採用するため、ここで計算しておく）
                    amount_val = clean_currency(amount_str)
                    
                    # 1列目のセル内改行を分割して、1行ずつチェック
                    lines = key_text_block.split('\n')
                    
                    for line in lines:
                        line = line.strip()
                        if is_ignore_line(line):
                            continue
                        
                        # === ID行かどうかの判定 ===
                        # 行の先頭が数字6桁以上であれば、新しい人の始まり
                        if re.match(r'^\d{6,}', line) and '/' not in line:
                            
                            # IDと名前に分離
                            user_id, user_name = split_id_name(line)
                            
                            # 新しいレコードを作成
                            current_record = {
                                "id": user_id,
                                "name": user_name,
                                "remarks": [],     # 備考はリストで溜める
                                "amount_val": amount_val # 金額はこの行のものを使う
                            }
                            extracted_records.append(current_record)
                        
                        else:
                            # === ID行ではない場合 ===
                            # 直前に読み込んだ人がいれば、その人の備考として追加
                            if current_record is not None:
                                current_record["remarks"].append(line)
                            else:
                                # まだ誰も読み込んでいないのに文字がある（ヘッダーの残りなど）→無視
                                pass
    
    # リスト形式をDataFrameに変換
    data_list = []
    for rec in extracted_records:
        data_list.append({
            "id": rec["id"],
            "name": rec["name"],
            "remarks": " ".join(rec["remarks"]), # 備考リストを結合
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
            merged['is_new'] = merged['amount_val_prev'].isna()
            merged['is_diff'] = (~merged['is_new']) & (merged['amount_val_curr'] != merged['amount_val_prev'])
            merged['is_same'] = (~merged['is_new']) & (merged['amount_val_curr'] == merged['amount_val_prev'])

            # 5. 表示用データの整形
            def format_num(val):
                return f"{int(val):,}" if pd.notnull(val) else ""

            display_df = merged.copy()
            display_df['今回請求額'] = display_df['amount_val_curr'].apply(format_num)
            display_df['前回請求額'] = display_df['amount_val_prev'].apply(format_num)
            
            final_view = display_df[['id', 'name', 'remarks', '今回請求額', '前回請求額', 'is_new', 'is_diff', 'is_same']].copy()
            final_view.columns = ['ID', '利用者名', '備考', '今回請求額', '前回請求額', 'is_new', 'is_diff', 'is_same']

            # ==========================================
            # スタイリング
            # ==========================================
            def highlight_rows(row):
                styles = [''] * len(row)
                
                # ケース1: 新規 -> 背景薄黄色
                if row['is_new']:
                    return ['background-color: #ffffe0; color: black;'] * len(row)
                
                # ケース2: 一致 -> 文字色グレー
                if row['is_same']:
                    return ['color: #d3d3d3;'] * len(row)

                # ケース3: 金額相違 -> 赤/青
                if row['is_diff']:
                    styles[0] = 'color: black;' 
                    styles[1] = 'color: black;' 
                    styles[2] = 'color: black;' 
                    styles[3] = 'color: red; font-weight: bold; background-color: #ffe6e6;' 
                    styles[4] = 'color: blue; font-weight: bold;' 
                
                return styles

            st.markdown("### 判定結果")
            st.info("背景黄色：今回のみ(新規) / 文字グレー：前回と一致 / 赤青：金額変更")
            
            styled_df = final_view.style.apply(highlight_rows, axis=1)
            
            # フラグ列を非表示
            styled_df = styled_df.hide(axis="columns", subset=['is_new', 'is_diff', 'is_same'])

            st.dataframe(
                styled_df,
                use_container_width=True,
                height=800,
                column_config={
                    "ID": st.column_config.TextColumn("ID"),
                }
            )
            
            csv_data = final_view.drop(columns=['is_new', 'is_diff', 'is_same'])
            st.download_button(
                "結果をCSVでダウンロード",
                csv_data.to_csv(index=False).encode('utf-8-sig'),
                "check_result.csv"
            )
