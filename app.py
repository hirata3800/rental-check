import streamlit as st
import pdfplumber
import pandas as pd
import re

# ==========================================
# ページ設定 & UI非表示
# ==========================================
st.set_page_config(page_title="請求書チェックツール", layout="wide")
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
    # 「1, 200」のように間にスペースが入るケースにも対応
    s = str(x).replace(',', '').replace('円', '').replace('¥', '').replace(' ', '').strip()
    try:
        return int(s)
    except:
        return 0

def extract_text_mode(file):
    """
    PDFを見たままの「テキスト」として読み込み、行ごとのパターンで解析する
    """
    all_records = []
    current_record = None
    
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            # extract_text() で「見たままの文字列」を取得
            text = page.extract_text()
            if not text:
                continue
                
            lines = text.split('\n')
            
            for line in lines:
                line = line.strip()
                if not line: continue
                
                # ヘッダー行などはスキップ
                if "ページ" in line or "請求書チェックリスト" in line or "未収金額" in line:
                    continue

                # ========================================================
                # 【判定ロジック】 お客様のテキスト法則に完全準拠
                # パターン: [数字6桁以上] ... [数字とカンマ(金額)]
                # ========================================================
                
                # 行末の金額パターンを探す
                # ^(\d{6,})   : 行頭に6桁以上の数字(ID)
                # .*?         : 途中の文字(名前)
                # ([\d, ]+)$  : 行末に「数字・カンマ・スペース」だけの塊があるか(金額)
                
                match = re.match(r'^(\d{6,})\s+(.*?)\s+([\d, ]+)$', line)
                
                is_valid_user_line = False
                
                if match:
                    user_id = match.group(1)
                    user_name = match.group(2).strip()
                    amount_str = match.group(3)
                    amount_val = clean_currency(amount_str)
                    
                    # 金額が0より大きければ「利用者行」と確定
                    if amount_val > 0:
                        is_valid_user_line = True
                        
                        # 名前にサイクル文字(6ヶ月など)が混ざっていたら消す
                        cycle_match = re.search(r'(\d+\s*(?:ヶ月|年))', user_name)
                        cycle_text = ""
                        if cycle_match:
                            cycle_text = cycle_match.group(1)
                            user_name = user_name.replace(cycle_text, "").strip()

                        current_record = {
                            "id": user_id,
                            "name": user_name,
                            "cycle": cycle_text,
                            "remarks": [],
                            "amount_val": amount_val
                        }
                        all_records.append(current_record)

                if not is_valid_user_line:
                    # 利用者行でなければ、それは「備考」か「サイクル行」
                    # IDで始まっていても、金額がなければここに来るので備考になる
                    if current_record:
                        # "6ヶ月" だけの行かチェック
                        if re.fullmatch(r'\d+\s*(?:ヶ月|年)', line):
                            if not current_record["cycle"]:
                                current_record["cycle"] = line
                        else:
                            # それ以外は全て備考に追加
                            # サイクル文字が混ざっていたら除去して追加
                            cycle_match = re.search(r'(\d+\s*(?:ヶ月|年))', line)
                            if cycle_match:
                                cycle_text_in_line = cycle_match.group(1)
                                # サイクル情報がまだなければ取得
                                if not current_record["cycle"]:
                                    current_record["cycle"] = cycle_text_in_line
                                # 備考本文からは削除
                                line = line.replace(cycle_text_in_line, "").strip()
                            
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
        # テキストモードで抽出
        df_current = extract_text_mode(file_current)
        df_prev = extract_text_mode(file_prev)

        if df_current.empty or df_prev.empty:
            st.error("有効なデータが見つかりませんでした。")
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
            def format_curr(val):
                return f"{int(val):,}" if pd.notnull(val) else "0"
            def format_prev(val):
                return f"{int(val):,}" if pd.notnull(val) else "該当なし"

            display_df = merged.copy()
            display_df['今回請求額'] = display_df['amount_val_curr'].apply(format_curr)
            display_df['前回請求額'] = display_df['amount_val_prev'].apply(format_prev)
            
            final_view = display_df[['id', 'name', 'cycle', 'remarks', '今回請求額', '前回請求額', 'is_new', 'is_diff', 'is_same']].copy()
            final_view.columns = ['ID', '利用者名', '請求サイクル', '備考', '今回請求額', '前回請求額', 'is_new', 'is_diff', 'is_same']

            # 色付け設定
            def highlight_rows(row):
                styles = ['color: black'] * len(row)
                
                if row['is_new']:
                    # 新規は黒文字のまま
                    pass
                elif row['is_same']:
                    # 一致はグレー
                    styles = ['color: #d3d3d3'] * len(row)
                elif row['is_diff']:
                    # 変更は赤青強調
                    styles[4] = 'color: red; font-weight: bold; background-color: #ffe6e6'
                    styles[5] = 'color: blue; font-weight: bold'
                
                # 備考に「◆請◆」があれば行全体を黄色
                if '◆請◆' in str(row['備考']):
                    # 既に色指定がある場合は背景色だけ上書き
                    for i in range(len(styles)):
                        if 'background-color' not in styles[i]:
                            styles[i] += '; background-color: #ffffcc'
                        else:
                            # 既存の背景色があればそのまま（赤背景優先）
                            pass
                            
                return styles

            st.markdown("### 判定結果")
            st.caption("文字グレー：前回と一致 / 赤青：金額変更")
            
            styled_df = final_view.style.apply(highlight_rows, axis=1)

            st.dataframe(
                styled_df,
                use_container_width=True,
                height=800,
                column_config={"ID": st.column_config.TextColumn("ID")},
                column_order=['ID', '利用者名', '請求サイクル', '備考', '今回請求額', '前回請求額']
            )
            
            st.download_button(
                "結果をCSVでダウンロード",
                final_view.to_csv(index=False).encode('utf-8-sig'),
                "check_result.csv"
            )
