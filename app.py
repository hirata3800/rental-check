import streamlit as st
from pypdf import PdfReader
import pandas as pd
import re
import datetime
import gc

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
    s = str(x).replace(',', '').replace('円', '').replace('¥', '').replace(' ', '').strip()
    try:
        return int(s)
    except:
        return 0

@st.cache_data(ttl=600)  # 10分間キャッシュして再計算を防ぐ
def extract_text_mode(file):
    """
    PDFをテキストとして解析する（軽量版）
    """
    all_records = []
    
    try:
        reader = PdfReader(file)
        # ページごとにテキスト抽出してすぐ解放
        for page in reader.pages:
            text = page.extract_text()
            if not text: continue
            
            lines = text.split('\n')
            
            # 行ごとの処理
            for line in lines:
                line = line.strip()
                if not line: continue
                
                # 無視する行
                if "ページ" in line or "請求書チェックリスト" in line or "未収金額" in line or "合計" in line:
                    continue
                
                match = re.match(r'^(\d{6,})\s+(.*?)\s+([\d,]+)$', line)
                
                if match:
                    user_id = match.group(1)
                    raw_name_part = match.group(2).strip()
                    amount_str = match.group(3)
                    amount_val = clean_currency(amount_str)
                    
                    if amount_val > 0:
                        # 名前のクリーニング
                        uncollected_match = re.search(r'([\d,]+)$', raw_name_part)
                        if uncollected_match:
                            possible_money = clean_currency(uncollected_match.group(1))
                            if possible_money > 0:
                                raw_name_part = raw_name_part[:uncollected_match.start()].strip()

                        # サイクル文字の除去
                        cycle_text = ""
                        cycle_match = re.search(r'(\d+\s*(?:ヶ月|年))', raw_name_part)
                        if cycle_match:
                            cycle_text = cycle_match.group(1)
                            raw_name_part = raw_name_part.replace(cycle_text, "").strip()
                        
                        # NGワード
                        ng_keywords = ["様の", "奥様", "ご主人", "回収", "集金"]
                        if any(kw in raw_name_part for kw in ng_keywords):
                            continue
                        else:
                            # 辞書のリストではなく、タプルのリストにしてメモリ節約
                            # (ID, Name, Cycle, Remarks, Amount)
                            # ※備考の結合処理は重いので、ここでは省略し、必要な行だけ取る簡易ロジックにします
                            all_records.append((user_id, raw_name_part, cycle_text, "", amount_val))

            # メモリ解放
            del text
            del lines
            
    except Exception as e:
        return pd.DataFrame(), []

    # 一気にDataFrame化
    df = pd.DataFrame(all_records, columns=["id", "name", "cycle", "remarks", "amount_val"])
    gc.collect()
    return df, []

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
        df_current, _ = extract_text_mode(file_current)
        df_prev, _ = extract_text_mode(file_prev)

        if df_current.empty or df_prev.empty:
            st.error("有効なデータが見つかりませんでした。")
        else:
            # 重複排除
            df_current = df_current.drop_duplicates(subset=['id'])
            df_prev = df_prev.drop_duplicates(subset=['id'])
            
            # 必要最小限のカラムでマージ
            merged = pd.merge(
                df_current, 
                df_prev[['id', 'amount_val']], 
                on='id', 
                how='left', 
                suffixes=('_curr', '_prev')
            )
            
            # メモリ節約のため元のDFを削除
            del df_current
            del df_prev
            gc.collect()
            
            # 判定ロジック
            merged['is_new'] = merged['amount_val_prev'].isna()
            merged['is_diff'] = (~merged['is_new']) & (merged['amount_val_curr'] != merged['amount_val_prev'])
            merged['is_same'] = (~merged['is_new']) & (merged['amount_val_curr'] == merged['amount_val_prev'])

            def format_curr(val): return f"{int(val):,}" if pd.notnull(val) else "0"
            def format_prev(val): return f"{int(val):,}" if pd.notnull(val) else "該当なし（新規）"

            merged['今回請求額'] = merged['amount_val_curr'].apply(format_curr)
            merged['前回請求額'] = merged['amount_val_prev'].apply(format_prev)
            
            # 表示用DF作成
            final_view = merged[['id', 'name', 'cycle', 'remarks', '今回請求額', '前回請求額', 'is_new', 'is_diff', 'is_same']].copy()
            final_view.columns = ['ID', '利用者名', '請求サイクル', '備考', '今回請求額', '前回請求額', 'is_new', 'is_diff', 'is_same']
            
            # No.列
            final_view.insert(0, 'No.', range(1, len(final_view) + 1))

            # CSV用データ作成（ここで作っておく）
            csv_export = final_view.copy()
            csv_export['ID'] = csv_export['ID'].apply(lambda x: f'="{x}"')
            csv_data = csv_export.to_csv(index=False).encode('utf-8-sig')
            
            # メモリ解放
            del csv_export
            gc.collect()

            # ダウンロードボタン
            now = datetime.datetime.now()
            file_name = f"{now.strftime('%Y%m%d%H%M%S')}.csv"
            
            c1, c2 = st.columns([3, 1])
            with c1:
                st.markdown("### 判定結果")
            with c2:
                st.write("")
                st.download_button(
                    "結果をCSVでダウンロード",
                    csv_data,
                    file_name,
                    mime='text/csv'
                )
            
            # === 【重要】表示行数を制限する（メモリ対策） ===
            st.info("※データ件数が多いため、画面には最初の100件のみ表示しています。（全データはCSVで確認できます）")
            
            # スタイリング関数
            def highlight_rows(row):
                styles = ['color: black'] * len(row)
                curr_idx = 5
                prev_idx = 6
                
                if row['is_same']:
                    styles[curr_idx] = 'color: #d3d3d3'
                    styles[prev_idx] = 'color: #d3d3d3'
                elif row['is_diff']:
                    styles[curr_idx] = 'color: red; font-weight: bold; background-color: #ffe6e6'
                    styles[prev_idx] = 'color: blue; font-weight: bold'
                elif row['is_new']:
                    styles[curr_idx] = 'color: red; font-weight: bold; background-color: #ffe6e6'
                
                return styles

            # 先頭100件だけ切り出して表示
            subset_view = final_view.head(100)
            
            st.dataframe(
                subset_view.style.apply(highlight_rows, axis=1),
                use_container_width=True,
                height=800,
                hide_index=True,
                column_config={
                    "No.": st.column_config.NumberColumn("No.", format="%d", width="small"),
                    "ID": st.column_config.TextColumn("ID"),
                },
                column_order=['No.', 'ID', '利用者名', '請求サイクル', '備考', '今回請求額', '前回請求額']
            )
            
            # 最後のお掃除
            gc.collect()
