import streamlit as st
import pdfplumber
import pandas as pd
import re

# --- ページ設定 ---
st.set_page_config(page_title="請求書チェックツール", layout="wide")

# --- UI非表示設定 ---
st.markdown("""
    <style>
    header, footer, [data-testid="stHeader"], [data-testid="stToolbar"], .stAppDeployButton {
        display: none !important;
        visibility: hidden !important;
    }
    div[data-testid="stDataFrame"] th {
        pointer-events: none !important;
        cursor: default !important;
    }
    .block-container {
        padding-top: 1rem !important;
    }
    </style>
""", unsafe_allow_html=True)

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

def get_amount_from_line(text):
    """行の末尾や文中にある金額を取得する"""
    # 1. まず日付っぽい数字を隠す (2025年, 9/1, 11月など)
    # これにより金額誤認を防ぐ
    masked_text = re.sub(r'\d{4}年', '', text)
    masked_text = re.sub(r'\d{1,2}/\d{1,2}', '', masked_text)
    masked_text = re.sub(r'\d{1,2}月', '', masked_text)
    
    # 2. 金額パターンを探す (カンマ付き、または「円」の前)
    # 行の「最後」に出てくる数値を金額として優先採用する
    # パターン: 1,200 または 1200
    matches = re.findall(r'(\d{1,3}(?:,\d{3})*)', masked_text)
    
    if matches:
        # 後ろから順番にチェック
        for m in reversed(matches):
            val_str = m.replace(',', '')
            if not val_str: continue
            try:
                val = int(val_str)
                # ID(6桁以上)と混同しないよう、かつ0円ではないもの
                # ※IDも数値だが、文脈的にIDは行頭で処理済みのはず。
                # ここでは「ID以外の数値」かつ「0より大きい」ものを探す
                if 0 < val < 10000000: # 1000万未満を金額とみなす(IDはもっと大きい)
                    return val
            except:
                continue
    return 0

def extract_text_mode(file):
    """
    PDFをテキストとして読み込み、行ごとのパターンで解析する
    """
    all_records = []
    current_record = None
    
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            # extract_text() を使用して、見たままのテキストを取得
            text = page.extract_text()
            if not text:
                continue
                
            lines = text.split('\n')
            
            for line in lines:
                line = line.strip()
                if not line: continue
                if "ページ" in line or "請求書チェックリスト" in line or "未収金額" in line:
                    continue

                # ========================================================
                # 【判定ロジック】
                # 1. IDで始まっているか？
                # 2. その行に「金額」があるか？
                #    YES -> 新しい利用者
                #    NO  -> 備考 (IDで始まっていても金額がなければ備考)
                # ========================================================
                
                # IDパターン (行頭の数字)
                id_match = re.match(r'^(\d{6,})', line)
                
                is_new_user = False
                amount_val = 0
                
                if id_match:
                    user_id = id_match.group(1)
                    # IDを除いた残りのテキスト
                    remaining_text = line[len(user_id):].strip()
                    
                    # 金額を探す
                    amount_val = get_amount_from_line(remaining_text)
                    
                    # 金額があれば利用者確定
                    if amount_val > 0:
                        is_new_user = True
                        
                        # 名前とサイクルの分離
                        # remaining_text には "黒崎誠 7,200" や "山崎和雄 6ヶ月 10,800" が入っている
                        
                        # まず金額文字を消す(一番後ろの数字)
                        amount_str_match = re.search(r'(\d{1,3}(?:,\d{3})*)\s*$', remaining_text)
                        if amount_str_match:
                            target_amt = amount_str_match.group(1)
                            # 金額と一致していれば消す
                            if int(target_amt.replace(',','')) == amount_val:
                                remaining_text = remaining_text[:amount_str_match.start()].strip()
                        
                        # サイクルを探す
                        cycle_text = ""
                        cycle_match = re.search(r'(\d+\s*(?:ヶ月|年))', remaining_text)
                        if cycle_match:
                            cycle_text = cycle_match.group(1)
                            # 名前からサイクルを消す
                            remaining_text = remaining_text.replace(cycle_text, "").strip()
                        
                        user_name = remaining_text.strip()
                        
                        # レコード作成
                        current_record = {
                            "id": user_id,
                            "name": user_name,
                            "cycle": cycle_text,
                            "remarks": [],
                            "amount_val": amount_val
                        }
                        all_records.append(current_record)
                    
                    else:
                        # IDで始まっているが金額がない -> 備考行 (例: 0100143486藤吉様の奥様)
                        if current_record:
                            current_record["remarks"].append(line)
                
                else:
                    # IDで始まらない行 -> 備考 または サイクルのみの行
                    if current_record:
                        # サイクルのみの行かチェック ("6ヶ月" だけなど)
                        if re.fullmatch(r'\d+\s*(?:ヶ月|年)', line):
                            # もし利用者のサイクルがまだ空ならセット、そうでなければ無視(重複)
                            if not current_record["cycle"]:
                                current_record["cycle"] = line
                        else:
                            # 純粋な備考
                            current_record["remarks"].append(line)

    # データフレーム化
    data_list = []
    for rec in all_records:
        data_list.append({
            "id": rec["id"],
            "name": rec["name"],
            "cycle": rec["cycle"],
            "remarks": " ".join(rec["remarks"]),
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
    with st.spinner('テキスト解析中...'):
        # 抽出ロジック切り替え
        df_current = extract_text_mode(file_current)
        df_prev = extract_text_mode(file_prev)

        if df_current.empty or df_prev.empty:
            st.error("有効なデータが見つかりませんでした。")
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
            
            merged['is_new'] = merged['amount_val_prev'].isna()
            merged['is_diff'] = (~merged['is_new']) & (merged['amount_val_curr'] != merged['amount_val_prev'])
            merged['is_same'] = (~merged['is_new']) & (merged['amount_val_curr'] == merged['amount_val_prev'])

            def format_curr(val):
                return f"{int(val):,}" if pd.notnull(val) else "0"
            def format_prev(val):
                return f"{int(val):,}" if pd.notnull(val) else "該当なし"

            display_df = merged.copy()
            display_df['今回請求額'] = display_df['amount_val_curr'].apply(format_curr)
            display_df['前回請求額'] = display_df['amount_val_prev'].apply(format_prev)
            
            final_view = display_df[['id', 'name', 'cycle', 'remarks', '今回請求額', '前回請求額', 'is_new', 'is_diff', 'is_same']].copy()
            final_view.columns = ['ID', '利用者名', '請求サイクル', '備考', '今回請求額', '前回請求額', 'is_new', 'is_diff', 'is_same']

            def highlight_rows(row):
                bg_color = 'white'
                text_color = 'black'
                
                # 備考に「◆請◆」があれば行全体を黄色に
                if '◆請◆' in str(row['備考']):
                    bg_color = '#ffffcc'
                
                base_style = f'background-color: {bg_color}; color: {text_color};'
                styles = [base_style] * len(row)
                
                if row['is_new']:
                    styles[4] = 'color: red; font-weight: bold; background-color: #ffe6e6;'
                elif row['is_same']:
                    grey_style = f'color: #a0a0a0; background-color: {bg_color};'
                    styles[4] = grey_style
                    styles[5] = grey_style
                elif row['is_diff']:
                    styles[4] = 'color: red; font-weight: bold; background-color: #ffe6e6;'
                    styles[5] = f'color: blue; font-weight: bold; background-color: {bg_color};'
                return styles

            st.markdown("### 判定結果")
            st.caption("備考に「◆請◆」あり：黄色 / 新規・変更：赤背景 / 一致：金額グレー")
            
            styled_df = final_view.style.apply(highlight_rows, axis=1)

            st.dataframe(
                styled_df,
                use_container_width=True,
                height=800,
                column_config={
                    "ID": st.column_config.TextColumn("ID"),
                },
                column_order=['ID', '利用者名', '請求サイクル', '備考', '今回請求額', '前回請求額']
            )
            
            st.download_button(
                "結果をCSVでダウンロード (全項目)",
                final_view.to_csv(index=False).encode('utf-8-sig'),
                "check_result.csv"
            )
