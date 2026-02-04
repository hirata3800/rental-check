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
    """金額文字列を数値に変換"""
    if not x: return 0
    s = str(x).replace(',', '').replace('円', '').replace('¥', '').replace(' ', '').strip()
    try:
        return int(s)
    except:
        return 0

def extract_text_mode(file):
    """
    PDFをテキストとして解析する
    """
    all_records = []
    current_record = None
    
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
                if "ページ" in line or "請求書チェックリスト" in line or "未収金額" in line or "合計" in line or "年月日締" in line:
                    continue
                
                # === 【修正点】正規表現を変更 ===
                # 以前: r'^(\d{6,})\s+(.*?)\s+([\d,]+)$' (行末は数字必須)
                # 今回: r'^(\d{6,})\s+(.*)\s+([0-9,]+)(.*)$' (数字の後ろに何かあってもOK)
                # ※金額部分を [0-9,]+ にして半角数字のみを対象にし、全角の「６」などが混ざらないように分離
                match = re.match(r'^(\d{6,})\s+(.*)\s+([0-9,]+)(.*)$', line)
                
                is_user_line = False
                
                if match:
                    user_id = match.group(1)
                    raw_name_part = match.group(2).strip()
                    amount_str = match.group(3)
                    trailing_part = match.group(4).strip() # 金額の後ろにくっついている文字
                    
                    amount_val = clean_currency(amount_str)
                    
                    if amount_val > 0:
                        is_user_line = True
                        
                        cycle_text = ""

                        # 1. 金額の後ろにくっついている文字からサイクルを探す（例：「9000６ヶ月」の「６ヶ月」）
                        if trailing_part:
                            cycle_match_trailing = re.search(r'(\d+\s*(?:ヶ月|年))', trailing_part)
                            if cycle_match_trailing:
                                cycle_text = cycle_match_trailing.group(1)

                        # 2. 名前のクリーニング（末尾の未収金額除去）
                        uncollected_match = re.search(r'([\d,]+)$', raw_name_part)
                        if uncollected_match:
                            possible_money = clean_currency(uncollected_match.group(1))
                            if possible_money > 0:
                                raw_name_part = raw_name_part[:uncollected_match.start()].strip()

                        # 3. 名前の中にサイクル文字が混ざっているか確認（除去するが、サイクルとしては trailing を優先）
                        cycle_match_name = re.search(r'(\d+\s*(?:ヶ月|年))', raw_name_part)
                        if cycle_match_name:
                            c_text_name = cycle_match_name.group(1)
                            raw_name_part = raw_name_part.replace(c_text_name, "").strip()
                            # まだサイクルが見つかってなければ採用
                            if not cycle_text:
                                cycle_text = c_text_name
                        
                        # NGワードチェック
                        ng_keywords = ["様の", "奥様", "ご主人", "回収", "集金"]
                        if any(kw in raw_name_part for kw in ng_keywords):
                            is_user_line = False
                        else:
                            current_record = {
                                "id": user_id,
                                "name": raw_name_part,
                                "cycle": cycle_text, 
                                "is_cycle_fixed": bool(cycle_text), # 行内で見つかったなら確定扱い
                                "remarks": [],
                                "amount_val": amount_val
                            }
                            all_records.append(current_record)

                if not is_user_line:
                    if current_record:
                        # サイクルだけの行の場合
                        if re.fullmatch(r'\d+\s*(?:ヶ月|年)', line):
                            current_record["cycle"] = line
                            current_record["is_cycle_fixed"] = True
                        else:
                            # 備考などはそのまま保存
                            current_record["remarks"].append(line)

            del text
            del lines
            gc.collect() 
            
    except Exception as e:
        return pd.DataFrame(), []

    # 辞書リストからDataFrameへ変換
    data_list = []
    for rec in all_records:
        data_list.append({
            "id": rec["id"],
            "name": rec["name"],
            "cycle": rec["cycle"],
            "remarks": " ".join(rec["remarks"]),
            "amount_val": rec["amount_val"]
        })

    df = pd.DataFrame(data_list)
    gc.collect()
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

if 'processed_data' not in st.session_state:
    st.session_state.processed_data = None

if file_current and file_prev:
    if st.session_state.processed_data is None:
        with st.spinner('解析中...'):
            df_current = extract_text_mode(file_current)
            gc.collect()
            df_prev = extract_text_mode(file_prev)
            gc.collect()

            if df_current.empty or df_prev.empty:
                st.error("データが見つかりませんでした。")
            else:
                df_current = df_current.drop_duplicates(subset=['id'])
                df_prev = df_prev.drop_duplicates(subset=['id'])
                
                merged = pd.merge(
                    df_current, 
                    df_prev[['id', 'amount_val']], 
                    on='id', 
                    how='left', 
                    suffixes=('_curr', '_prev')
                )
                
                del df_current
                del df_prev
                gc.collect()
                
                merged['is_new'] = merged['amount_val_prev'].isna()
                merged['is_diff'] = (~merged['is_new']) & (merged['amount_val_curr'] != merged['amount_val_prev'])
                merged['is_same'] = (~merged['is_new']) & (merged['amount_val_curr'] == merged['amount_val_prev'])

                def format_curr(val): return f"{int(val):,}" if pd.notnull(val) else "0"
                def format_prev(val): return f"{int(val):,}" if pd.notnull(val) else "該当なし（新規）"

                merged['今回請求額'] = merged['amount_val_curr'].apply(format_curr)
                merged['前回請求額'] = merged['amount_val_prev'].apply(format_prev)
                
                final_view = merged[['id', 'name', 'cycle', 'remarks', '今回請求額', '前回請求額', 'is_new', 'is_diff', 'is_same']].copy()
                final_view.columns = ['ID', '利用者名', '請求サイクル', '備考', '今回請求額', '前回請求額', 'is_new', 'is_diff', 'is_same']
                final_view.insert(0, 'No.', range(1, len(final_view) + 1))
                
                st.session_state.processed_data = final_view
                gc.collect()

    if st.session_state.processed_data is not None:
        final_view = st.session_state.processed_data
        
        csv_export = final_view.copy()
        csv_export['ID'] = csv_export['ID'].apply(lambda x: f'="{x}"')
        csv_data = csv_export.to_csv(index=False).encode('utf-8-sig')
        
        now = datetime.datetime.now()
        file_name = f"{now.strftime('%Y%m%d%H%M%S')}.csv"
        
        st.download_button(
            label="📥 結果をCSVでダウンロード",
            data=csv_data,
            file_name=file_name,
            mime='text/csv'
        )
        
        st.divider()
        
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
            
            if '◆請◆' in str(row['備考']):
                for i in range(len(styles)):
                    if 'background-color' not in styles[i]:
                        styles[i] += '; background-color: #ffffcc'
                        
            return styles

        ROWS_PER_PAGE = 100
        total_rows = len(final_view)
        total_pages = (total_rows - 1) // ROWS_PER_PAGE + 1
        
        c1, c2, c3 = st.columns([2, 2, 6])
        with c1:
            st.markdown(f"**全 {total_rows} 件**")
        with c2:
            current_page = st.number_input(
                "ページ選択", 
                min_value=1, 
                max_value=total_pages, 
                value=1
            )
        
        start_idx = (current_page - 1) * ROWS_PER_PAGE
        end_idx = start_idx + ROWS_PER_PAGE
        subset_view = final_view.iloc[start_idx:end_idx]
        
        st.caption(f"{start_idx + 1} 〜 {min(end_idx, total_rows)} 件目を表示中")

        st.dataframe(
            subset_view.style.apply(highlight_rows, axis=1),
            use_container_width=True,
            height=800,
            hide_index=True,
            column_config={
                "No.": st.column_config.NumberColumn("No.", format="%d", width="small"),
                "ID": st.column_config.TextColumn("ID"),
                "is_new": None,
                "is_diff": None,
                "is_same": None
            }
        )
        
        gc.collect()

else:
    st.session_state.processed_data = None
