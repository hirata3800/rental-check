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

# UI設定
st.markdown("""
    <style>
    header, footer, [data-testid="stHeader"], [data-testid="stToolbar"], .stAppDeployButton {
        display: none !important;
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
    if not x: return 0
    s = str(x).replace(',', '').replace('円', '').replace('¥', '').replace(' ', '').strip()
    try:
        return int(s)
    except:
        return 0

# 【変更点】キャッシュ(@st.cache_data)を削除してメモリ消費を抑える
def extract_text_mode(file):
    all_records = []
    
    try:
        reader = PdfReader(file)
        for page in reader.pages:
            text = page.extract_text()
            if not text: continue
            
            lines = text.split('\n')
            
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

                        # サイクル文字除去
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
                            # 必要な情報だけをタプルで保存（メモリ節約）
                            # ID, 名前, サイクル, 備考(空), 金額
                            all_records.append((user_id, raw_name_part, cycle_text, "", amount_val))

            # ループごとの掃除
            del text
            del lines
            
    except Exception as e:
        return pd.DataFrame(), []

    # DataFrame作成
    df = pd.DataFrame(all_records, columns=["id", "name", "cycle", "remarks", "amount_val"])
    gc.collect() # 掃除
    return df

# ==========================================
# アプリ画面
# ==========================================

st.title('📄 利用者請求額チェックツール')

col1, col2 = st.columns(2)
with col1:
    file_current = st.file_uploader("① 今回請求分", type="pdf", key="m")
with col2:
    file_prev = st.file_uploader("② 前回請求分", type="pdf", key="t")

if file_current and file_prev:
    with st.spinner('解析中...'):
        # 1つずつ処理してはメモリを捨てる
        df_current = extract_text_mode(file_current)
        gc.collect()
        
        df_prev = extract_text_mode(file_prev)
        gc.collect()

        if df_current.empty or df_prev.empty:
            st.error("データが見つかりませんでした。")
        else:
            # 重複排除
            df_current = df_current.drop_duplicates(subset=['id'])
            df_prev = df_prev.drop_duplicates(subset=['id'])
            
            # マージ
            merged = pd.merge(
                df_current, 
                df_prev[['id', 'amount_val']], 
                on='id', 
                how='left', 
                suffixes=('_curr', '_prev')
            )
            
            # 元データ削除
            del df_current
            del df_prev
            gc.collect()
            
            # 判定
            merged['is_new'] = merged['amount_val_prev'].isna()
            merged['is_diff'] = (~merged['is_new']) & (merged['amount_val_curr'] != merged['amount_val_prev'])
            merged['is_same'] = (~merged['is_new']) & (merged['amount_val_curr'] == merged['amount_val_prev'])

            # 整形
            def format_curr(val): return f"{int(val):,}" if pd.notnull(val) else "0"
            def format_prev(val): return f"{int(val):,}" if pd.notnull(val) else "該当なし（新規）"

            merged['今回請求額'] = merged['amount_val_curr'].apply(format_curr)
            merged['前回請求額'] = merged['amount_val_prev'].apply(format_prev)
            
            # 表示用
            final_view = merged[['id', 'name', 'cycle', 'remarks', '今回請求額', '前回請求額', 'is_new', 'is_diff', 'is_same']].copy()
            final_view.columns = ['ID', '利用者名', '請求サイクル', '備考', '今回請求額', '前回請求額', 'is_new', 'is_diff', 'is_same']
            final_view.insert(0, 'No.', range(1, len(final_view) + 1))

            # ▼▼▼ まずCSVボタンを作る（最優先） ▼▼▼
            csv_export = final_view.copy()
            csv_export['ID'] = csv_export['ID'].apply(lambda x: f'="{x}"')
            csv_data = csv_export.to_csv(index=False).encode('utf-8-sig')
            
            # CSV作成に使ったメモリを解放
            del csv_export
            gc.collect()

            now = datetime.datetime.now()
            file_name = f"{now.strftime('%Y%m%d%H%M%S')}.csv"
            
            st.success("解析完了！以下のボタンから結果をダウンロードしてください。")
            
            st.download_button(
                label="📥 結果をCSVでダウンロード",
                data=csv_data,
                file_name=file_name,
                mime='text/csv'
            )
            
            st.divider()
            
            # ▼▼▼ 画面表示（色付けなし・軽量版） ▼▼▼
            st.caption("※負荷軽減のため、画面上の色分けを停止し、先頭50件のみ表示しています。全データはCSVで確認してください。")
            
            # 最初の50行だけ表示（スタイル適用なし＝超軽量）
            st.dataframe(
                final_view.head(50), 
                use_container_width=True,
                hide_index=True
            )
            
            gc.collect()
