import streamlit as st
import pdfplumber
import pandas as pd

# --- ページ設定 ---
st.set_page_config(page_title="請求書チェックツール", layout="wide")

# --- 簡易認証機能 ---
def check_password():
    """パスワード認証が成功したらTrueを返す"""
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
# メイン機能
# ==========================================

# --- 関数: PDFからテキスト/表データを抽出 (改良版) ---
def extract_data_from_pdf(file):
    all_data = []
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            # 1. まず標準設定（罫線あり）で読み取りを試みる
            tables = page.extract_tables()
            
            # 2. もし読み取れなかった場合、設定を変えて（罫線なし/空白区切り）読み取る
            if not tables:
                tables = page.extract_tables(table_settings={
                    "vertical_strategy": "text", 
                    "horizontal_strategy": "text",
                    "snap_tolerance": 5
                })
            
            for table in tables:
                for row in table:
                    # 改行コードなどを除去
                    cleaned_row = [str(cell).replace('\n', '').strip() if cell else '' for cell in row]
                    all_data.append(cleaned_row)
                    
    if not all_data:
        return pd.DataFrame()
    df = pd.DataFrame(all_data)
    return df

# --- 関数: 金額の数値化 ---
def clean_currency(x):
    if isinstance(x, str):
        clean_str = x.replace(',', '').replace('円', '').replace('¥', '').strip()
        # 全角数字なども考慮して変換
        if clean_str.replace('-', '').isnumeric():
            return int(clean_str)
    return x

# --- アプリ画面 ---
st.title('📄 レンタル伝票 差異チェックツール')
st.markdown("---")

col1, col2 = st.columns(2)
with col1:
    st.subheader("① 正本 (Master)")
    file_master = st.file_uploader("ファイルをアップロード", type="pdf", key="master")
with col2:
    st.subheader("② 比較対象 (Target)")
    file_target = st.file_uploader("ファイルをアップロード", type="pdf", key="target")

if file_master and file_target:
    with st.spinner('PDFを解析中...'):
        try:
            df1 = extract_data_from_pdf(file_master)
            df2 = extract_data_from_pdf(file_target)

            if df1.empty or df2.empty:
                st.error("エラー: PDFから表データが見つかりませんでした。スキャン画像の場合は読み取れません。")
            else:
                st.success("読み込み完了！設定を行ってください。")
                
                with st.expander("読み込んだデータのプレビュー"):
                    st.write("データサンプル:", df1.head(3))

                st.markdown("### 比較設定")
                
                col_options = df1.columns.tolist()
                
                c1, c2 = st.columns(2)
                with c1:
                    # 利用者名の列を推測（一番データの種類が多い列など）
                    key_col = st.selectbox("キーとなる列（利用者IDや氏名）", col_options, index=0)
                with c2:
                    # 金額列を推測（後ろの方の列）
                    target_col = st.selectbox("比較する金額の列", col_options, index=len(col_options)-1)

                if st.button("チェック実行", type="primary"):
                    merged = pd.merge(df1, df2, on=key_col, how='inner', suffixes=('_正', '_誤'))
                    
                    val_col_1 = f"{target_col}_正"
                    val_col_2 = f"{target_col}_誤"
                    
                    merged[val_col_1] = merged[val_col_1].apply(clean_currency)
                    merged[val_col_2] = merged[val_col_2].apply(clean_currency)

                    merged_numeric = merged[pd.to_numeric(merged[val_col_1], errors='coerce').notnull() & 
                                          pd.to_numeric(merged[val_col_2], errors='coerce').notnull()]

                    diff_df = merged_numeric[merged_numeric[val_col_1] != merged_numeric[val_col_2]].copy()
                    
                    st.markdown("---")
                    st.subheader("判定結果")
                    
                    if not diff_df.empty:
                        st.error(f"⚠️ {len(diff_df)} 件の差異が見つかりました")
                        result_view = diff_df[[key_col, val_col_1, val_col_2]].copy()
                        result_view.columns = ["キー項目", "正本の金額", "比較対象の金額"]
                        result_view["差額"] = result_view["正本の金額"] - result_view["比較対象の金額"]
                        st.dataframe(result_view, use_container_width=True)
                        csv = result_view.to_csv(index=False).encode('utf-8-sig')
                        st.download_button("結果をCSVでダウンロード", csv, "check_result.csv", "text/csv")
                    else:
                        st.balloons()
                        st.success("🎉 すべての金額が一致しました！")

        except Exception as e:
            st.error(f"予期せぬエラー: {e}")
