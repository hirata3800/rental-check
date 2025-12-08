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
# データ処理ロジック
# ==========================================

def clean_currency(x):
    """金額を数値に変換（日付やIDの誤認を徹底排除）"""
    if not isinstance(x, str): return 0
    s = str(x).replace(',', '').replace('円', '').replace('¥', '').strip()
    
    # 日付っぽいもの（スラッシュ、年号）は金額ではない
    if '/' in s or '202' in s: return 0
    
    try:
        # 純粋な数値のみ抽出
        match = re.search(r'-?\d+', s)
        if match:
            val = int(match.group())
            # 異常値チェック（IDを金額と誤認するのを防ぐため、100万円以上は無視）
            if abs(val) > 1000000: return 0
            return val
    except: pass
    return 0

def extract_from_pdf(file):
    """PDFからデータを抽出する（テーブルモード強化版）"""
    data_list = []
    
    # ★ここが最強のフィルターです★
    # これらが名前に含まれていたら、金額が入っていても「備考」とみなして弾きます
    ng_keywords = [
        "様", "奥", "主", "夫", "妻", "娘", "息", "族", "親", 
        "回収", "集金", "亡", "同時", "義", "（", "(", "→", "別", "居宅"
    ]

    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            # テーブルとして抽出
            tables = page.extract_tables()
            
            for table in tables:
                for row in table:
                    # 空行スキップ
                    if not any(row): continue
                    
                    # データをきれいにする
                    row = [str(cell).strip() if cell is not None else "" for cell in row]
                    
                    # 列が少なすぎる行は無視
                    if len(row) < 2: continue

                    # --- 各列の特定 ---
                    # [0]: ID, [1]: 名前 ... 最後: 金額 という構成を想定
                    raw_id = row[0]
                    raw_name = row[1] if len(row) > 1 else ""
                    raw_amount = row[-1] if len(row) > 0 else "0"

                    # 1. IDチェック（6桁以上の数字があるか）
                    if not re.match(r'^\d{6,}', raw_id):
                        continue

                    # 2. 金額チェック（0円は除外）
                    amount_val = clean_currency(raw_amount)
                    if amount_val == 0:
                        continue

                    # 3. 【決定打】名前NGワードチェック
                    # 金額があっても、名前に「様」などが入っていたら除外する
                    if any(kw in raw_name for kw in ng_keywords):
                        continue

                    # 4. サイクル文字の除去（名前に混ざっている場合）
                    cycle_text = ""
                    # 行全体からサイクルを探す
                    for cell in row:
                        match = re.search(r'(\d+\s*(?:ヶ月|年))', cell)
                        if match:
                            cycle_text = match.group(1)
                            if cycle_text in raw_name:
                                raw_name = raw_name.replace(cycle_text, "").strip()
                            break

                    # 全ての関門を突破した行だけを登録
                    data_list.append({
                        "id": raw_id,
                        "name": raw_name,
                        "cycle": cycle_text,
                        "remarks": "", # 備考は今回はシンプル化のため省略（ID誤検知防止を最優先）
                        "amount_val": amount_val
                    })

    # 重複排除（同じIDが複数回出た場合、最初の1つを採用）
    df = pd.DataFrame(data_list)
    if not df.empty:
        df = df.drop_duplicates(subset=['id'])
    
    return df

# ==========================================
# アプリ画面構成
# ==========================================

st.title('📄 レンタル伝票 差異チェックツール')
st.caption("①今回分を基準に、②前回分と比較します。")

col1, col2 = st.columns(2)
with col1:
    file_current = st.file_uploader("① 今回請求分 (Current)", type="pdf", key="m")
with col2:
    file_prev = st.file_uploader("② 前回請求分 (Previous)", type="pdf", key="t")

if file_current and file_prev:
    with st.spinner('解析中...'):
        # データ抽出
        df_curr = extract_from_pdf(file_current)
        df_prev = extract_from_pdf(file_prev)

        if df_curr.empty or df_prev.empty:
            st.error("有効なデータが見つかりませんでした。PDFの形式を確認してください。")
        else:
            # 結合処理
            merged = pd.merge(
                df_curr, 
                df_prev[['id', 'amount_val']], 
                on='id', 
                how='left', 
                suffixes=('_curr', '_prev')
            )
            
            # 判定フラグ作成
            merged['is_new'] = merged['amount_val_prev'].isna()
            merged['is_diff'] = (~merged['is_new']) & (merged['amount_val_curr'] != merged['amount_val_prev'])
            merged['is_same'] = (~merged['is_new']) & (merged['amount_val_curr'] == merged['amount_val_prev'])

            # 表示用フォーマット
            def fmt(x): return f"{int(x):,}" if pd.notnull(x) else ""
            
            merged['今回請求額'] = merged['amount_val_curr'].apply(fmt)
            merged['前回請求額'] = merged['amount_val_prev'].apply(lambda x: fmt(x) if pd.notnull(x) else "該当なし")

            # 表示用データフレーム
            final_view = merged[['id', 'name', 'cycle', 'remarks', '今回請求額', '前回請求額', 'is_new', 'is_diff', 'is_same']].copy()
            final_view.columns = ['ID', '利用者名', '請求サイクル', '備考', '今回請求額', '前回請求額', 'is_new', 'is_diff', 'is_same']

            # スタイリング関数
            def highlight_rows(row):
                styles = ['color: black'] * len(row) # デフォルト黒
                
                if row['is_new']:
                    # 新規：背景白（指定なし）、文字黒
                    pass
                elif row['is_same']:
                    # 一致：文字グレー
                    styles = ['color: #d3d3d3'] * len(row)
                elif row['is_diff']:
                    # 変更：金額を赤/青強調
                    styles[4] = 'color: red; font-weight: bold; background-color: #ffe6e6' # 今回
                    styles[5] = 'color: blue; font-weight: bold' # 前回
                
                return styles

            st.markdown("### 判定結果")
            st.info("文字グレー：前回と一致 / 赤青：金額変更")

            # テーブル表示（並び替え禁止設定済み）
            st.dataframe(
                final_view.style.apply(highlight_rows, axis=1),
                use_container_width=True,
                height=800,
                column_config={"ID": st.column_config.TextColumn("ID")},
                column_order=['ID', '利用者名', '請求サイクル', '備考', '今回請求額', '前回請求額']
            )

            # CSVダウンロード
            csv = final_view.to_csv(index=False).encode('utf-8-sig')
            st.download_button("結果をCSVでダウンロード", csv, "check_result.csv")
