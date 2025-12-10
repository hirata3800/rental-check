import streamlit as st
import pdfplumber
import pandas as pd
import re

# ==========================================
# ページ設定
# ==========================================
st.set_page_config(page_title="利用者請求額チェックツール", layout="wide")

# UIの余計な表示を消す設定
st.markdown("""
    <style>
    header, footer, [data-testid="stHeader"], [data-testid="stToolbar"], .stAppDeployButton {
        display: none !important;
    }
    div[data-testid="stDataFrame"] th {
        pointer-events: none !important;
        cursor: default !important;
    }
    .block-container {
        padding-top: 1rem !important;
    }
    /* ボタンの位置調整 */
    div.stButton {text-align: right;}
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 認証機能
# ==========================================
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
    if not x: return 0
    # 「7, 200」のようなスペース入りも除去
    s = str(x).replace(',', '').replace('円', '').replace('¥', '').replace(' ', '').strip()
    try:
        return int(s)
    except:
        return 0

def extract_text_mode(file):
    """
    PDFを「見たままテキスト」として解析する
    """
    all_records = []
    current_record = None
    
    # デバッグ用：読み取った生データを保存
    raw_lines_debug = []

    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            # layout=True で、できるだけ見た目通りの配置でテキスト化
            text = page.extract_text(layout=True)
            if not text:
                continue
            
            lines = text.split('\n')
            
            for line in lines:
                line = line.strip()
                if not line: continue
                
                # ヘッダーやページ番号は無視
                if "ページ" in line or "請求書チェックリスト" in line or "未収金額" in line:
                    continue
                
                raw_lines_debug.append(line) # デバッグ用に保存

                # -----------------------------------------------------------
                # 【判定ロジック】
                # 行末に金額があるID行だけを狙い撃ちする
                # 正規表現: 行頭に数字(ID) ... 途中はなんでも ... 行末に数字(金額)
                # -----------------------------------------------------------
                
                # ^(\d{6,})   : ID（6桁以上）
                # \s+         : 空白（必須！これで備考の「ID直結文字」を弾く）
                # (.*?)       : 名前
                # \s+         : 空白
                # ([\d,]+)$   : 金額（行末）
                match = re.match(r'^(\d{6,})\s+(.*?)\s+([\d,]+)$', line)
                
                is_user_line = False
                
                if match:
                    user_id = match.group(1)
                    raw_name = match.group(2).strip()
                    amount_str = match.group(3)
                    amount_val = clean_currency(amount_str)
                    
                    # 金額が0より大きいなら利用者確定
                    if amount_val > 0:
                        is_user_line = True
                        
                        # 名前にサイクル文字が混ざっていたら消す
                        cycle_text = ""
                        cycle_match = re.search(r'(\d+\s*(?:ヶ月|年))', raw_name)
                        if cycle_match:
                            cycle_text = cycle_match.group(1)
                            raw_name = raw_name.replace(cycle_text, "").strip()
                        
                        # 名前にNGワードが含まれていないか最終チェック
                        # (金額があっても、名前に「様」が入っていたら備考とみなす)
                        ng_keywords = ["様の", "奥様", "ご主人", "回収", "集金"]
                        if any(kw in raw_name for kw in ng_keywords):
                            is_user_line = False # 却下
                        else:
                            # 採用
                            current_record = {
                                "id": user_id,
                                "name": raw_name,
                                "cycle": cycle_text,
                                "remarks": [],
                                "amount_val": amount_val
                            }
                            all_records.append(current_record)

                if not is_user_line:
                    # 利用者行でなければ、それは「備考」か「サイクル行」
                    if current_record:
                        # サイクル行かチェック ("6ヶ月" だけの行)
                        if re.fullmatch(r'\d+\s*(?:ヶ月|年)', line):
                            if not current_record["cycle"]:
                                current_record["cycle"] = line
                        else:
                            # それ以外は全て備考
                            # サイクル文字が混ざっていたら除去
                            cycle_match_in_remark = re.search(r'(\d+\s*(?:ヶ月|年))', line)
                            if cycle_match_in_remark:
                                c_text = cycle_match_in_remark.group(1)
                                if not current_record["cycle"]:
                                    current_record["cycle"] = c_text
                                line = line.replace(c_text, "").strip()
                            
                            if line:
                                current_record["remarks"].append(line)

    # DataFrame化
    data_list = []
    for rec in all_records:
        data_list.append({
            "id": rec["id"],
            "name": rec["name"],
            "cycle": rec["cycle"],
            "remarks": " ".join(rec["remarks"]),
            "amount_val": rec["amount_val"]
        })
        
    return pd.DataFrame(data_list), raw_lines_debug

# ==========================================
# アプリ画面
# ==========================================

st.title('📄 利用者請求額チェックツール')
st.caption("①今回分を基準に、②前回分と比較します。")

col1, col2 = st.columns(2)
with col1:
    file_current = st.file_uploader("① 今回請求分", type="pdf", key="m")
with col2:
    file_prev = st.file_uploader("② 前回請求分", type="pdf", key="t")

if file_current and file_prev:
    with st.spinner('解析中...'):
        # 抽出
        df_current, debug_curr = extract_text_mode(file_current)
        df_prev, debug_prev = extract_text_mode(file_prev)

        if df_current.empty or df_prev.empty:
            st.error("有効なデータが見つかりませんでした。")
            # デバッグ用に読み取ったテキストを表示（何が起きているか確認用）
            with st.expander("詳細：読み取ったテキストデータを確認する"):
                st.write("▼ ①の読み取り結果（先頭20行）")
                st.write(debug_curr[:20])
        else:
            # 重複排除
            df_current = df_current.drop_duplicates(subset=['id'])
            df_prev = df_prev.drop_duplicates(subset=['id'])
            
            # 結合
            merged = pd.merge(
                df_current, 
                df_prev[['id', 'amount_val']], 
                on='id', 
                how='left', 
                suffixes=('_curr', '_prev')
            )
            
            # 判定
            merged['is_new'] = merged['amount_val_prev'].isna()
            merged['is_diff'] = (~merged['is_new']) & (merged['amount_val_curr'] != merged['amount_val_prev'])
            merged['is_same'] = (~merged['is_new']) & (merged['amount_val_curr'] == merged['amount_val_prev'])

            # 表示整形
            def format_curr(val): return f"{int(val):,}" if pd.notnull(val) else "0"
            def format_prev(val): return f"{int(val):,}" if pd.notnull(val) else "該当なし（新規）"

            display_df = merged.copy()
            display_df['今回請求額'] = display_df['amount_val_curr'].apply(format_curr)
            display_df['前回請求額'] = display_df['amount_val_prev'].apply(format_prev)
            
            final_view = display_df[['id', 'name', 'cycle', 'remarks', '今回請求額', '前回請求額', 'is_new', 'is_diff', 'is_same']].copy()
            final_view.columns = ['ID', '利用者名', '請求サイクル', '備考', '今回請求額', '前回請求額', 'is_new', 'is_diff', 'is_same']

            # スタイリング
            def highlight_rows(row):
                styles = ['color: black'] * len(row)
                
                if row['is_same']:
                    # 金額一致：今回と前回の金額列のみグレー
                    styles[4] = 'color: #d3d3d3' # 今回請求額
                    styles[5] = 'color: #d3d3d3' # 前回請求額
                elif row['is_diff']:
                    # 金額不一致
                    styles[4] = 'color: red; font-weight: bold; background-color: #ffe6e6'
                    styles[5] = 'color: blue; font-weight: bold'
                elif row['is_new']:
                    # 新規の場合（前回なし）
                    styles[4] = 'color: red; font-weight: bold; background-color: #ffe6e6'
                
                # 備考に「◆請◆」があれば黄色
                if '◆請◆' in str(row['備考']):
                    for i in range(len(styles)):
                        if 'background-color' not in styles[i]:
                            styles[i] += '; background-color: #ffffcc'
                            
                return styles

            # ----------------------------------------------------
            # レイアウト変更：タイトルとダウンロードボタンを横並びに
            # ----------------------------------------------------
            c1, c2 = st.columns([3, 1])
            with c1:
                st.markdown("### 判定結果")
            with c2:
                st.write("") # 高さ調整用
                st.download_button(
                    "結果をCSVでダウンロード",
                    final_view.to_csv(index=False).encode('utf-8-sig'),
                    "check_result.csv"
                )

            st.dataframe(
                final_view.style.apply(highlight_rows, axis=1),
                use_container_width=True,
                height=800,
                column_config={"ID": st.column_config.TextColumn("ID")},
                column_order=['ID', '利用者名', '請求サイクル', '備考', '今回請求額', '前回請求額']
            )
