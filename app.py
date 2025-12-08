import streamlit as st
import pdfplumber
import pandas as pd
import re

# --- ページ設定 ---
st.set_page_config(page_title="請求書チェックツール", layout="wide")

# --- UI非表示（必要に応じて編集） ---
st.markdown("""
    <style>
    header, footer, [data-testid="stHeader"], [data-testid="stToolbar"], .stAppDeployButton {
        display: none !important;
        visibility: hidden !important;
    }
    .block-container { padding-top: 1rem !important; }
    </style>
""", unsafe_allow_html=True)

# --- 簡易認証（任意） ---
def check_password():
    if "password_correct" not in st.session_state:
        st.session_state.password_correct = False
    if st.session_state.password_correct:
        return True
    st.title("🔒 ログイン")
    password = st.text_input("パスワードを入力してください", type="password")
    if st.button("ログイン"):
        # st.secrets["APP_PASSWORD"] にパスワードを入れてください
        if "APP_PASSWORD" in st.secrets and password == st.secrets["APP_PASSWORD"]:
            st.session_state.password_correct = True
            st.rerun()
        else:
            st.error("パスワードが違います")
    return False

# コメントアウトしたい場合は以下を True にして下さい
if not check_password():
    st.stop()


# ----------------------------
# ヘルパー関数
# ----------------------------
def clean_currency(x):
    """金額文字列を数値に変換（誤検出防止の安全策付き）"""
    if not isinstance(x, str):
        return 0
    s = x.split("\n")[0]
    # 日付や年号っぽい表現があれば金額ではない
    if '/' in s or re.search(r'20\d{2}', s):
        return 0
    s = s.replace(',', '').replace('円', '').replace('¥', '').replace(' ', '').strip()
    s = s.translate(str.maketrans('０１２３４５６７８９', '0123456789'))
    m = re.search(r'-?\d+', s)
    if not m:
        return 0
    try:
        v = int(m.group())
        # 想定上限（安全マージン）
        if abs(v) > 500000:
            return 0
        return v
    except:
        return 0

def is_ignore_line(line):
    line = (line or "").strip()
    if not line:
        return True
    if any(tok in line for tok in ["ページ", "請求サイクル", "未収金額", "利用者名", "請求額"]):
        return True
    if re.match(r'^\d{4}/\d{1,2}/\d{1,2}$', line):
        return True
    return False

def normalize_name_text(s):
    """括弧などを除去して評価しやすくする"""
    if s is None:
        return ""
    # 全角・半角の丸括弧内を削る
    s = re.sub(r'（.*?）', '', s)
    s = re.sub(r'\(.*?\)', '', s)
    # 矢印などの注記を削る
    s = re.sub(r'→.*', '', s)
    # 余分な記号を除去
    s = s.strip(" -–—:：・、,")
    return s.strip()

def looks_like_person_name(s):
    """日本語の氏名っぽさを判定（最低限のルール）"""
    if not s: 
        return False
    # 数字や英字が多いものは除外
    if re.search(r'[A-Za-z0-9]', s):
        return False
    # 漢字 or ひらがな/カタカナが2文字以上あること（簡易）
    if re.search(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]{2,}', s):
        return True
    return False

# ----------------------------
# メインパーサー（改良版）
# ----------------------------
def extract_detailed_format(file):

    # 明示的に「備考」扱いとしたいキーワード（これらを含む名前は備考）
    NG_WORDS = [
        "様", "様の", "奥様", "奥", "夫", "妻", "娘", "息子", "息",
        "親", "義", "回収", "集金", "亡", "同時", "回収済", "回収→", "集金→",
        "ﾚﾝﾀﾙなし", "レンタルなし"
    ]

    data_list = []
    last_record = None  # 最後に作った利用者レコード（備考紐づけ用）

    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables(table_settings={
                "vertical_strategy": "text",
                "horizontal_strategy": "text",
                "snap_tolerance": 3
            })

            for table in tables:
                for row in table:
                    if not any(row):
                        continue

                    # セル結合（右端が金額のはず）
                    cells = [str(c).strip() if c is not None else "" for c in row]
                    non_empty = [c for c in cells if c != ""]

                    if not non_empty:
                        continue

                    # 金額は最後のセル（あるいは右から最初に金額が見つかるセル）
                    amount_val = 0
                    # 探して見つかった最右の金額候補を使う（列数不定にやや頑強）
                    for c in reversed(non_empty[-3:]):  # 右端3列までをチェック
                        tmp = clean_currency(c)
                        if tmp > 0:
                            amount_val = tmp
                            break
                    # （注）現状、ユーザーから「今回請求額は0にならない」とのことなので、
                    # amount_val==0 は備考扱いにするのが安全

                    # キーとなるテキスト部分（左端の説明ブロック）
                    key_text = non_empty[0]
                    # 行中に複数行が入っている場合もある
                    lines = [ln.strip() for ln in key_text.split("\n") if ln.strip()]

                    # まず、もし amount_val == 0 なら「備考扱い」
                    if amount_val == 0:
                        # 備考は直近の last_record に付与（なければ無視）
                        for ln in lines:
                            if is_ignore_line(ln):
                                continue
                            cleaned = normalize_name_text(ln)
                            if last_record is not None and cleaned:
                                last_record["remarks"].append(cleaned)
                        continue

                    # amount_val > 0: 利用者候補の行
                    # ID を行中で探す（先頭だけではなく行のどこでも）
                    # IDは6桁以上の連続数字と仮定
                    id_search = re.search(r'(\d{6,})', key_text)
                    if not id_search:
                        # IDが無い行は備考扱い（直近レコードへ付与）
                        for ln in lines:
                            if is_ignore_line(ln): 
                                continue
                            cleaned = normalize_name_text(ln)
                            if last_record is not None and cleaned:
                                last_record["remarks"].append(cleaned)
                        continue

                    user_id = id_search.group(1)
                    # IDの後ろに続くテキストを名前候補とする（ID直後 or IDの次のトークン）
                    # 例: "0100143486藤吉様の奥様(同時に集金→回収)"
                    after_id = key_text[id_search.end():].strip()
                    # もし after_id が空なら、行内でIDの左側に名前がある可能性をチェック
                    if not after_id:
                        # IDの前半部分（ID直前のトークン）
                        before_id = key_text[:id_search.start()].strip()
                        name_candidate = before_id
                    else:
                        name_candidate = after_id

                    # 正規化（括弧や注記類を除去）
                    name_candidate_clean = normalize_name_text(name_candidate)

                    # NGワードが含まれる場合は**必ず備考**にする
                    if any(ng in name_candidate_clean for ng in NG_WORDS):
                        # 備考に追加（直近利用者がいればそこへ、いなければ新規"備考のみ"扱いをスキップ）
                        if last_record is not None:
                            last_record["remarks"].append(f"{user_id} {name_candidate_clean}")
                        continue

                    # 名前らしさチェック（漢字/かなが2文字以上など）
                    if not looks_like_person_name(name_candidate_clean):
                        # 人名らしくない → 備考扱い
                        if last_record is not None:
                            last_record["remarks"].append(f"{user_id} {name_candidate_clean}")
                        continue

                    # ここまで来たら「正規利用者」と判断
                    rec = {
                        "id": user_id,
                        "name": name_candidate_clean,
                        "cycle": "",     # 後で埋める（ページ内の別セルから取る場合はここで取る）
                        "remarks": [],
                        "amount_val": amount_val
                    }

                    # 請求サイクルが別セルにあるときに拾う（行内のセル全体を参照）
                    # 非常に単純なルール：行の中で「数字＋ヶ月/年」があれば cycle に入れる
                    cycle_found = None
                    for c in non_empty:
                        m = re.search(r'(\d+\s*(?:ヶ月|か月|月|年))', c)
                        if m:
                            cycle_found = m.group(1)
                            break
                    if cycle_found:
                        rec["cycle"] = cycle_found

                    data_list.append(rec)
                    last_record = rec

                    # 行の中で ID のあとに続く補足があれば備考に入れる
                    # 例: "0100123456 山田太郎 11月～入院" のような残りの情報
                    # (今回は name_candidate_clean は既に ID直後の文字列の一部なので
                    # 追加情報は他の cells から取得)
                    # ここでは、右隣セル（非金額）のテキストを簡易に remarks に追加
                    # （行データが複数セルに分かれているPDFがあるため）
                    if len(non_empty) > 1:
                        # 最右の金額セル以外の残りセルを備考とする
                        for extra in non_empty[1:-1]:
                            extra_clean = normalize_name_text(extra)
                            if extra_clean and not re.search(r'\d', extra_clean):
                                rec["remarks"].append(extra_clean)

    # DataFrame 化
    final = []
    for r in data_list:
        final.append({
            "id": r["id"],
            "name": r["name"],
            "cycle": r.get("cycle", ""),
            "remarks": " ".join(r.get("remarks", [])).strip(),
            "amount_val": r.get("amount_val", 0)
        })

    return pd.DataFrame(final)


# ----------------------------
# Streamlit UI
# ----------------------------
st.title("📄 レンタル伝票 差異チェックツール（改良版）")
st.caption("①今回分を基準に、②前回分と比較します。")

col1, col2 = st.columns(2)
with col1:
    file_current = st.file_uploader("① 今回請求分 (Current)", type="pdf", key="m")
with col2:
    file_prev = st.file_uploader("② 前回請求分 (Previous)", type="pdf", key="t")

if file_current and file_prev:
    with st.spinner("比較中..."):
        df_current = extract_detailed_format(file_current)
        df_prev = extract_detailed_format(file_prev)

        if df_current.empty or df_prev.empty:
            st.error("有効なデータが見つかりませんでした。")
        else:
            # 重複IDは最新行（先出）でユニーク化
            df_current = df_current.drop_duplicates(subset=['id'], keep='first')
            df_prev = df_prev.drop_duplicates(subset=['id'], keep='first')

            merged = pd.merge(df_current, df_prev[['id','amount_val']], on='id', how='left', suffixes=('_curr','_prev'))
            merged['is_new'] = merged['amount_val_prev'].isna()
            merged['is_diff'] = (~merged['is_new']) & (merged['amount_val_curr'] != merged['amount_val_prev'])
            merged['is_same'] = (~merged['is_new']) & (merged['amount_val_curr'] == merged['amount_val_prev'])

            def fmt(v): return f"{int(v):,}" if pd.notnull(v) else "該当なし"
            view = merged.copy()
            view['今回請求額'] = view['amount_val_curr'].apply(fmt)
            view['前回請求額'] = view['amount_val_prev'].apply(lambda x: fmt(x) if pd.notnull(x) else "該当なし")

            out = view[['id','name','cycle','remarks','今回請求額','前回請求額','is_new','is_diff','is_same']].copy()
            out.columns = ['ID','利用者名','請求サイクル','備考','今回請求額','前回請求額','is_new','is_diff','is_same']

            def highlight(row):
                styles = [''] * len(row)
                # 色付けは列インデックスに依存するため固定列順に合わせて設定
                if row['is_new']:
                    styles[4] = 'background-color:#ffe6e6; color:red; font-weight:bold;'
                elif row['is_same']:
                    styles[4] = styles[5] = 'color:#999;'
                elif row['is_diff']:
                    styles[4] = 'background-color:#ffe6e6; color:red; font-weight:bold;'
                    styles[5] = 'color:blue; font-weight:bold;'
                # 備考に「◆請◆」があれば行別背景
                if '◆請◆' in str(row['備考']):
                    for i in range(len(styles)):
                        if styles[i] == '':
                            styles[i] = 'background-color:#ffffcc;'
                # 空セルは白で初期化
                for i in range(len(styles)):
                    if styles[i] == '':
                        styles[i] = 'background-color:white; color:black;'
                return styles

            st.markdown("### 判定結果")
            st.caption("備考に『◆請◆』あり：黄色 / 新規・変更：赤背景 / 一致：金額グレー")

            styled = out.style.apply(highlight, axis=1)
            st.dataframe(styled, use_container_width=True, height=800)

            st.download_button("結果をCSVでダウンロード", out.to_csv(index=False).encode('utf-8-sig'), "check_result.csv")
