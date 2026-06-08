import re
import os
import json
import copy
import time
import gc
from html import escape
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import yfinance as yf

# ===== Streamlit UI 基本設定（一定要放最前面）=====
st.set_page_config(layout="wide")

# ===== 常數設定 =====
REFRESH_SEC = 30
ENABLE_GAP_SIGNAL = True
GROUP_EDIT_PIN = "1219"
GROUPS_FILE = "stock_groups.json"
BACKUP_DIR = "backups"
STOCK_NAME_FILE = "TWstocklistname.txt"

DEFAULT_STOCK_GROUPS = {
    "權值股": [
        "2330.TW", "00981A.TW", "2449.TW", "2317.TW", "3711.TW",
        "6488.TWO", "2327.TW", "6176.TW", "2303.TW", "5347.TWO",
    ],
    "自選股1": [
        "3008.TW", "3035.TW", "4566.TW", "4956.TW", "6456.TW",
        "4749.TWO", "6271.TW", "6290.TWO", "4919.TW"
    ],
    "低軌衛星": [
        "6285.TW", "2313.TW",
    ],
    "ABF": [
        "4958.TW", "3037.TW", "8046.TW", "3189.TW",
        "8996.TW", "5439.TWO", "8358.TWO",
    ],
    "記憶體": [
        "6770.TW", "2408.TW", "2344.TW", "8271.TW",
        "4967.TW", "3260.TWO", "2451.TW",
    ],
    "CCL": [
        "2383.TW", "6274.TWO", "6213.TW", "8039.TW"
    ],
    "CPO": [
        "4979.TWO", "3163.TWO", "4977.TW",
        "3081.TWO", "3450.TW", "6442.TW"
    ],
}

# ===== CSS =====
st.markdown("""
<style>
/* 儀表板外層：手機可左右滑動 */
.dashboard-scroll {
    overflow-x: auto;
    overflow-y: hidden;
    width: 100%;
    padding-bottom: 8px;
}

/* 固定 4 欄 */
.dashboard-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(260px, 1fr));
    gap: 12px;
    min-width: 1120px;
}

/* 卡片 */
.dashboard-card {
    border-radius: 12px;
    padding: 14px 16px;
    min-height: 180px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
    box-sizing: border-box;
}

/* 卡片標題：固定黑色 */
.dashboard-title {
    font-size: 18px;
    font-weight: 700;
    margin-bottom: 10px;
    color: #000000 !important;
}

/* 卡片主數字：保留 accent_color，不在這裡鎖黑 */
.dashboard-main {
    font-size: 28px;
    font-weight: 800;
    margin-bottom: 6px;
}

/* 卡片副說明：固定黑色 */
.dashboard-sub {
    font-size: 14px;
    color: #000000 !important;
    margin-bottom: 10px;
}

/* 卡片明細：固定黑色 */
.dashboard-detail {
    font-size: 14px;
    line-height: 1.7;
    color: #000000 !important;
}

/* 儀表板補充資訊 */
.dashboard-extra {
    font-size: 13px;
    line-height: 1.6;
    color: #000000 !important;
    margin-top: 10px;
    padding-top: 8px;
    border-top: 1px solid rgba(0,0,0,0.12);
    word-break: break-word;
}

/* 儀表板連結避免吃到 theme 顏色 */
.dashboard-link,
.dashboard-link:link,
.dashboard-link:visited,
.dashboard-link:hover,
.dashboard-link:active {
    text-decoration: none !important;
    color: inherit !important;
}

/* 回到儀表板按鈕 */
.back-to-dashboard-btn {
    display: inline-block;
    padding: 6px 12px;
    border-radius: 8px;
    border: 1px solid #999;
    background: #f5f5f5;
    color: #000 !important;
    text-decoration: none !important;
    font-size: 14px;
    font-weight: 600;
    text-align: center;
}

.back-to-dashboard-btn:hover {
    background: #eaeaea;
}
</style>
""", unsafe_allow_html=True)

# ===== 分組讀寫 =====
def load_stock_groups():
    """
    優先讀取本地 JSON，若失敗則載入預設值
    """
    if os.path.exists(GROUPS_FILE):
        try:
            with open(GROUPS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data:
                return data
        except Exception:
            pass

    return copy.deepcopy(DEFAULT_STOCK_GROUPS)


def save_stock_groups(groups):
    """
    將分組儲存到本地 JSON
    """
    with open(GROUPS_FILE, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)


def ensure_backup_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)


def create_backup_filename():
    tw_now = datetime.now(ZoneInfo("Asia/Taipei"))
    return f"stock_groups_backup_{tw_now.strftime('%Y%m%d_%H%M%S')}.json"


def save_backup_snapshot(groups):
    """
    建立本地備份檔
    """
    ensure_backup_dir()
    filename = create_backup_filename()
    file_path = os.path.join(BACKUP_DIR, filename)

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)

    return file_path


def list_backup_files():
    """
    列出最近備份檔（新到舊）
    """
    if not os.path.exists(BACKUP_DIR):
        return []

    files = []
    for name in os.listdir(BACKUP_DIR):
        if name.lower().endswith(".json"):
            full_path = os.path.join(BACKUP_DIR, name)
            if os.path.isfile(full_path):
                files.append((name, os.path.getmtime(full_path)))

    files.sort(key=lambda x: x[1], reverse=True)
    return [name for name, _ in files]


# ===== 工具函式 =====
def make_anchor_id(group_name: str) -> str:
    """
    將分類名稱轉成可當 HTML anchor 的 id
    """
    anchor = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", group_name).strip("-")
    return f"group-{anchor}"


def yahoo_quote_url(symbol: str) -> str:
    """
    產生台股 Yahoo 個股頁連結
    """
    return f"https://tw.stock.yahoo.com/quote/{symbol}"


@st.cache_data(ttl=86400)
def load_stock_name_map(file_path: str = STOCK_NAME_FILE) -> dict:
    """
    從本地 TWstocklistname.txt 載入股票名稱對照表
    格式支援：
    1101.TW    台泥
    2330.TW    台積電
    （tab 或多空白分隔皆可）
    """
    name_map = {}

    if not os.path.exists(file_path):
        return name_map

    with open(file_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue

            # 去掉 BOM / 全形空白
            line = line.replace("\ufeff", "").replace("\u3000", "")

            if "\t" in line:
                parts = line.split("\t")
                parts = [p.strip() for p in parts if p.strip()]
                if len(parts) >= 2:
                    symbol = parts[0].upper()
                    name = parts[1].strip()
                    name_map[symbol] = name
                    continue

            # fallback：多空白切兩欄
            m = re.match(r"^([^\s]+)\s+(.+)$", line)
            if m:
                symbol = m.group(1).strip().upper()
                name = m.group(2).strip()
                name_map[symbol] = name

    return name_map


@st.cache_data(ttl=86400)
def get_stock_name(symbol: str) -> str:
    """
    取得股票名稱：
    1. 先吃本地 txt 對照表（中文名稱優先）
    2. 再用 yfinance 抓 shortName / longName / displayName / name
    3. 最後 fallback 為代碼主體
    """
    name_map = load_stock_name_map(STOCK_NAME_FILE)

    if symbol in name_map:
        return name_map[symbol]

    try:
        ticker = yf.Ticker(symbol)

        info = {}
        try:
            info = ticker.get_info()
        except Exception:
            try:
                info = ticker.info
            except Exception:
                info = {}

        for key in ["shortName", "longName", "displayName", "name"]:
            val = info.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()

    except Exception:
        pass

    return symbol.split(".")[0]


def normalize_symbols_from_text(text: str):
    """
    將文字區輸入轉成股票代碼清單
    支援：
    - 一行一檔
    - 半形逗號
    - 全形逗號
    """
    if not text:
        return []

    text = text.replace("，", ",")
    lines = []

    for raw_line in text.splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        parts = [p.strip().upper() for p in raw_line.split(",") if p.strip()]
        lines.extend(parts)

    # 去重但保留順序
    seen = set()
    result = []
    for s in lines:
        if s not in seen:
            seen.add(s)
            result.append(s)

    return result


def validate_and_normalize_group_json(data):
    """
    驗證匯入 JSON 格式，並正規化成：
    {
        "分類名稱": ["2330.TW", "2317.TW", ...]
    }
    """
    if not isinstance(data, dict) or not data:
        raise ValueError("JSON 格式錯誤：最外層必須是非空物件（dict）")

    validated = {}

    for group_name, symbols in data.items():
        group_name = str(group_name).strip()
        if not group_name:
            raise ValueError("JSON 格式錯誤：分類名稱不可為空")

        if isinstance(symbols, list):
            raw_text = "\n".join(str(x) for x in symbols)
        elif isinstance(symbols, str):
            raw_text = symbols
        else:
            raise ValueError(f"JSON 格式錯誤：分類「{group_name}」的股票清單必須是 list 或 string")

        normalized_symbols = normalize_symbols_from_text(raw_text)
        validated[group_name] = normalized_symbols

    if not validated:
        raise ValueError("JSON 內容為空")

    return validated


def normalize_symbol_quick(input_text: str):
    """
    快速輸入股票代碼時，自動補上 .TW / .TWO
    簡單規則：
    - 已有 .TW / .TWO 直接沿用
    - 純數字且以 3 / 6 / 8 開頭，預設視為上櫃 .TWO
    - 其他預設 .TW
    """
    s = str(input_text).strip().upper()

    if not s:
        return None

    if "." in s:
        return s

    if s.isdigit():
        if s.startswith(("3", "6", "8")):
            return f"{s}.TWO"
        return f"{s}.TW"

    return s


def set_next_selected_group(group_name: str):
    """
    避免 widget 已建立後直接修改 selected_group_editor
    """
    st.session_state._next_selected_group = group_name


def enter_edit_mode():
    """
    進入編輯模式
    """
    st.session_state.editing_mode = True


def leave_edit_mode():
    """
    離開編輯模式
    """
    st.session_state.editing_mode = False


def symbol_to_code(symbol: str) -> str:
    """
    2330.TW -> 2330
    """
    return str(symbol).split(".")[0]


def format_pct_plain(val) -> str:
    """
    格式化為 +5.5% / -0.5%
    """
    try:
        num = float(val)
        return f"{num:+.1f}%"
    except Exception:
        return "-"


def build_top3_html(valid_stock_stats):
    """
    依照漲跌幅排序，取前三名，並產生 HTML：
    - 股票代碼 / 名稱：維持黑色
    - 上漲百分比：紅字
    - 下跌百分比：綠字
    - 平盤百分比：黑字
    """
    if not valid_stock_stats:
        return '<span style="color:#666666;">無可用資料</span>'

    top3_sorted = sorted(valid_stock_stats, key=lambda x: x["pct"], reverse=True)[:3]

    parts = []
    for item in top3_sorted:
        pct = float(item["pct"])

        if pct > 0:
            pct_color = "#cf1322"   # 漲：紅
        elif pct < 0:
            pct_color = "#389e0d"   # 跌：綠
        else:
            pct_color = "#333333"   # 平盤：深灰

        code_text = escape(str(item["code"]))
        name_text = escape(str(item["name"]))
        pct_text = f"{pct:+.1f}%"

        parts.append(
            f'<span style="color:#000000;">{code_text} {name_text} </span>'
            f'<span style="color:{pct_color}; font-weight:600;">{pct_text}</span>'
        )

    return " | ".join(parts)


def compact_name_list(names, max_show=3):
    """
    多個股票名稱縮短顯示
    """
    names = [str(x).strip() for x in names if str(x).strip()]
    if not names:
        return "無"

    if len(names) <= max_show:
        return "、".join(names)

    return "、".join(names[:max_show]) + f" 等{len(names)}檔"


# ===== Session State 初始化 =====
if "auto_refresh_enabled" not in st.session_state:
    st.session_state.auto_refresh_enabled = False

if "stock_groups" not in st.session_state:
    st.session_state.stock_groups = load_stock_groups()

if "group_editor_unlocked" not in st.session_state:
    st.session_state.group_editor_unlocked = False

if "editing_mode" not in st.session_state:
    st.session_state.editing_mode = False

if "selected_group_editor" not in st.session_state:
    group_names_init = list(st.session_state.stock_groups.keys())
    st.session_state.selected_group_editor = group_names_init[0] if group_names_init else ""

if "rename_group_input" not in st.session_state:
    st.session_state.rename_group_input = st.session_state.selected_group_editor

if "symbols_text_area" not in st.session_state:
    selected = st.session_state.selected_group_editor
    st.session_state.symbols_text_area = "\n".join(
        st.session_state.stock_groups.get(selected, [])
    )

if "quick_add_symbol_input" not in st.session_state:
    st.session_state.quick_add_symbol_input = ""


# ===== 延後切換 selected_group（避免 widget state 錯誤）=====
if "_next_selected_group" in st.session_state:
    pending_group = st.session_state._next_selected_group
    del st.session_state._next_selected_group

    if pending_group in st.session_state.stock_groups:
        st.session_state.selected_group_editor = pending_group
        st.session_state.rename_group_input = pending_group
        st.session_state.symbols_text_area = "\n".join(
            st.session_state.stock_groups.get(pending_group, [])
        )


def sync_editor_fields_from_selected_group():
    """
    當切換分類時，同步編輯欄位內容
    """
    groups = st.session_state.stock_groups
    selected_group = st.session_state.selected_group_editor

    if selected_group not in groups:
        group_names = list(groups.keys())
        if group_names:
            selected_group = group_names[0]
            st.session_state.selected_group_editor = selected_group
        else:
            selected_group = ""

    st.session_state.rename_group_input = selected_group
    st.session_state.symbols_text_area = "\n".join(groups.get(selected_group, []))
    st.session_state.editing_mode = False


# ===== 分組編輯鎖 =====
def render_group_editor_lock():
    """
    Sidebar 的 PIN 驗證鎖
    """
    st.sidebar.markdown("## 🔐 分組編輯鎖")

    if st.session_state.group_editor_unlocked:
        st.sidebar.success("已解鎖，可編輯股票分組")
        st.sidebar.info("為避免編輯中被重刷，分組編輯解鎖時會暫停自動更新")
        if st.sidebar.button("鎖定編輯", key="lock_group_editor_btn", use_container_width=True):
            st.session_state.group_editor_unlocked = False
            leave_edit_mode()
            st.rerun()
        return

    pin_input = st.sidebar.text_input(
        "請輸入 PIN 碼以編輯分組",
        type="password",
        key="group_edit_pin_input"
    )

    if st.sidebar.button("解鎖編輯", key="unlock_group_editor_btn", use_container_width=True):
        if pin_input == GROUP_EDIT_PIN:
            st.session_state.group_editor_unlocked = True
            enter_edit_mode()
            st.sidebar.success("PIN 正確，已解鎖")
            st.rerun()
        else:
            st.sidebar.error("PIN 錯誤")


def render_stock_group_editor():
    """
    Sidebar 的股票分組編輯介面
    """
    st.sidebar.markdown("## 🛠️ 股票分組編輯")

    groups = st.session_state.stock_groups
    group_names = list(groups.keys())

    if not group_names:
        st.session_state.stock_groups = copy.deepcopy(DEFAULT_STOCK_GROUPS)
        groups = st.session_state.stock_groups
        group_names = list(groups.keys())

    # 保證 selected_group 合法（在 widget 建立前處理）
    if st.session_state.selected_group_editor not in group_names:
        first_group = group_names[0]
        st.session_state.selected_group_editor = first_group
        st.session_state.rename_group_input = first_group
        st.session_state.symbols_text_area = "\n".join(groups.get(first_group, []))

    # ===== 新增分類 =====
    with st.sidebar.expander("➕ 新增分類", expanded=False):
        new_group_name = st.text_input("分類名稱", key="new_group_name_input")

        if st.button("新增分類", key="add_group_btn", use_container_width=True):
            enter_edit_mode()
            name = new_group_name.strip()

            if not name:
                st.sidebar.warning("請輸入分類名稱")
            elif name in groups:
                st.sidebar.warning("分類名稱已存在")
            else:
                groups[name] = []
                st.session_state.stock_groups = groups
                save_stock_groups(groups)

                # 新增後切到新分類（用延後切換）
                set_next_selected_group(name)
                st.rerun()

    # ===== 編輯既有分類 =====
    with st.sidebar.expander("📝 編輯分類", expanded=True):
        st.selectbox(
            "選擇分類",
            options=group_names,
            key="selected_group_editor",
            on_change=sync_editor_fields_from_selected_group
        )

        selected_group = st.session_state.selected_group_editor

        new_group_name = st.text_input(
            "分類名稱（可修改）",
            key="rename_group_input",
            on_change=enter_edit_mode
        )

        symbols_text = st.text_area(
            "股票清單（每行一檔，或逗號分隔）",
            height=220,
            key="symbols_text_area",
            on_change=enter_edit_mode
        )

        # ===== 快速新增股票搜尋 =====
        st.markdown("### ⚡ 快速新增股票搜尋")

        quick_col1, quick_col2 = st.columns([2, 1])

        with quick_col1:
            quick_input = st.text_input(
                "輸入股票代碼或 ticker（例如 2330、2330.TW、6488.TWO）",
                key="quick_add_symbol_input",
                on_change=enter_edit_mode
            )

        normalized_quick_symbol = normalize_symbol_quick(quick_input)

        if normalized_quick_symbol:
            st.caption(f"標準化代碼：{normalized_quick_symbol}")

        with quick_col2:
            if st.button("加入目前分類", key="quick_add_btn", use_container_width=True):
                enter_edit_mode()

                symbol = normalize_symbol_quick(quick_input)

                if not symbol:
                    st.warning("請輸入股票代碼")
                else:
                    current_list = groups.get(selected_group, [])

                    if symbol in current_list:
                        st.warning("此股票已存在於目前分類")
                    else:
                        current_list.append(symbol)
                        groups[selected_group] = current_list

                        st.session_state.stock_groups = groups
                        save_stock_groups(groups)

                        # 同步更新文字區
                        st.session_state.symbols_text_area = "\n".join(current_list)
                        st.session_state.quick_add_symbol_input = ""

                        st.success(f"已加入 {symbol}")
                        st.rerun()

        col1, col2 = st.columns(2)

        with col1:
            if st.button("💾 儲存分類", key="save_group_btn", use_container_width=True):
                new_name = new_group_name.strip()

                if not new_name:
                    st.sidebar.warning("分類名稱不可為空")
                elif new_name != selected_group and new_name in groups:
                    st.sidebar.warning("分類名稱已存在，請使用其他名稱")
                else:
                    new_symbols = normalize_symbols_from_text(symbols_text)

                    updated = {}
                    for k, v in groups.items():
                        if k == selected_group:
                            updated[new_name] = new_symbols
                        else:
                            updated[k] = v

                    st.session_state.stock_groups = updated
                    save_stock_groups(updated)

                    leave_edit_mode()

                    # 儲存後切到新名稱（用延後切換）
                    set_next_selected_group(new_name)
                    st.rerun()

        with col2:
            if st.button("🗑️ 刪除分類", key="delete_group_btn", use_container_width=True):
                if len(groups) <= 1:
                    st.sidebar.warning("至少保留一個分類")
                else:
                    groups.pop(selected_group, None)
                    st.session_state.stock_groups = groups
                    save_stock_groups(groups)

                    leave_edit_mode()

                    # 刪除後切到第一個分類（用延後切換）
                    remaining = list(groups.keys())
                    set_next_selected_group(remaining[0])
                    st.rerun()

    # ===== 備份 / 匯出 / 匯入 JSON =====
    with st.sidebar.expander("📦 備份 / 匯出 / 匯入 JSON", expanded=False):
        export_json_str = json.dumps(
            st.session_state.stock_groups,
            ensure_ascii=False,
            indent=2
        )

        st.download_button(
            label="⬇️ 匯出目前分組 JSON",
            data=export_json_str,
            file_name="stock_groups.json",
            mime="application/json",
            key="download_groups_json_btn",
            use_container_width=True
        )

        if st.button("🗂️ 建立本地備份", key="create_local_backup_btn", use_container_width=True):
            try:
                backup_file = save_backup_snapshot(st.session_state.stock_groups)
                st.sidebar.success(f"已建立備份：{os.path.basename(backup_file)}")
            except Exception as e:
                st.sidebar.error(f"建立備份失敗：{e}")

        uploaded_file = st.file_uploader(
            "上傳股票分組 JSON",
            type=["json"],
            key="upload_groups_json_file"
        )

        if uploaded_file is not None:
            st.caption("上傳後按下「匯入並覆蓋目前分組」才會生效")

            if st.button("📥 匯入並覆蓋目前分組", key="import_groups_json_btn", use_container_width=True):
                try:
                    raw = uploaded_file.read()
                    data = json.loads(raw.decode("utf-8"))
                    validated = validate_and_normalize_group_json(data)

                    # 匯入前先自動備份
                    save_backup_snapshot(st.session_state.stock_groups)

                    st.session_state.stock_groups = validated
                    save_stock_groups(validated)

                    leave_edit_mode()

                    # 匯入後同步選取狀態（延後切換）
                    first_group = list(validated.keys())[0]
                    set_next_selected_group(first_group)

                    st.sidebar.success("JSON 匯入成功，已覆蓋目前股票分組")
                    st.rerun()

                except Exception as e:
                    st.sidebar.error(f"JSON 匯入失敗：{e}")

        backups = list_backup_files()
        if backups:
            st.markdown("**最近備份檔**")
            for name in backups[:5]:
                st.caption(name)
        else:
            st.caption("目前沒有本地備份檔")

    # ===== 還原預設 =====
    with st.sidebar.expander("♻️ 重設", expanded=False):
        if st.button("還原預設分組", key="reset_groups_btn", use_container_width=True):
            try:
                save_backup_snapshot(st.session_state.stock_groups)
            except Exception:
                pass

            st.session_state.stock_groups = copy.deepcopy(DEFAULT_STOCK_GROUPS)
            save_stock_groups(st.session_state.stock_groups)

            leave_edit_mode()

            first_group = list(st.session_state.stock_groups.keys())[0]
            set_next_selected_group(first_group)

            st.rerun()

    # ===== 分組預覽 =====
    with st.sidebar.expander("👀 分組預覽", expanded=False):
        for g, symbols in st.session_state.stock_groups.items():
            st.markdown(f"**{g}**（{len(symbols)}檔）")
            st.caption(", ".join(symbols) if symbols else "（空）")


# ===== 快取：降低重複請求 =====
@st.cache_data(ttl=REFRESH_SEC)
def download_stock_data(symbol):
    df = yf.download(
        symbol,
        period="3mo",
        auto_adjust=True,
        progress=False
    )
    return df


# ===== 將 yfinance 回傳欄位整理成標準 OHLC =====
def normalize_ohlc(df):
    """
    將 yfinance 可能回傳的 MultiIndex 或一般欄位，
    統一整理成單層欄位：Open / High / Low / Close / Volume
    """
    if df is None or df.empty:
        return pd.DataFrame()

    required_cols = ["Open", "High", "Low", "Close", "Volume"]

    # 單層欄位
    if not isinstance(df.columns, pd.MultiIndex):
        cols = [c for c in required_cols if c in df.columns]
        if "Close" in cols and "High" in cols and "Low" in cols:
            return df[cols].copy()
        return pd.DataFrame()

    # MultiIndex 欄位
    normalized = pd.DataFrame(index=df.index)

    for target_col in required_cols:
        matched_series = None
        for col in df.columns:
            if isinstance(col, tuple) and target_col in col:
                matched_series = df[col]
                break

        if matched_series is not None:
            normalized[target_col] = matched_series

    if {"Close", "High", "Low"}.issubset(normalized.columns):
        return normalized

    return pd.DataFrame()


# ===== 取得價格：優先用 fast_info，抓不到就用最後收盤 =====
def get_last_price(symbol, df):
    try:
        ticker = yf.Ticker(symbol)
        price = ticker.fast_info.get("last_price", None)
        if price is not None and pd.notna(price):
            return float(price)
    except Exception:
        pass

    # fallback
    if not df.empty and "Close" in df.columns:
        return float(df["Close"].iloc[-1])

    raise ValueError("無法取得即時價格")


# ===== 技術指標計算 =====
def compute_indicators(df, price):
    if df is None or df.empty:
        raise ValueError("下載資料為空")

    if len(df) < 20:
        raise ValueError("歷史資料不足（至少需要 20 筆）")

    close = pd.to_numeric(df["Close"], errors="coerce")
    low = pd.to_numeric(df["Low"], errors="coerce")
    high = pd.to_numeric(df["High"], errors="coerce")

    if close.isna().all() or low.isna().all() or high.isna().all():
        raise ValueError("OHLC 資料格式異常")

    # ===== 漲跌 =====
    yesterday_close = close.iloc[-2]
    if pd.isna(yesterday_close) or yesterday_close == 0:
        raise ValueError("昨收資料異常")

    change_pct = (price / yesterday_close - 1) * 100

    # ===== MA =====
    ma5 = float(close.tail(5).mean())
    ma10 = float(close.tail(10).mean())
    ma20 = float(close.tail(20).mean())

    if price > ma5:
        ma_range = ">MA5"
    elif ma5 >= price > ma10:
        ma_range = "MA5~10"
    elif ma10 >= price > ma20:
        ma_range = "MA10~20"
    else:
        ma_range = "<MA20"

    if ma5 > ma10 > ma20:
        ma_trend = "多頭"
    elif ma5 < ma10 < ma20:
        ma_trend = "空頭"
    else:
        ma_trend = "糾結"

    # ===== KD =====
    low_9 = low.rolling(9).min()
    high_9 = high.rolling(9).max()
    denominator = (high_9 - low_9).replace(0, pd.NA)

    rsv = ((close - low_9) / denominator) * 100
    k = rsv.ewm(alpha=1/3, adjust=False).mean()
    d = k.ewm(alpha=1/3, adjust=False).mean()

    if len(k.dropna()) < 2 or len(d.dropna()) < 2:
        raise ValueError("KD 計算資料不足")

    k_t = float(k.iloc[-1])
    d_t = float(d.iloc[-1])
    k_y = float(k.iloc[-2])
    d_y = float(d.iloc[-2])

    # ===== KD 訊號 =====
    if k_y <= d_y and k_t > d_t:
        kd_signal = "黃金交叉"
    elif k_y >= d_y and k_t < d_t:
        kd_signal = "死亡交叉"
    elif k_t < d_t and (d_t - k_t) < 3:
        kd_signal = "即將黃金交叉"
    elif k_t > d_t and (k_t - d_t) < 3:
        kd_signal = "即將死亡交叉"
    elif k_t < 25:
        kd_signal = "超賣"
    else:
        kd_signal = "-"

    # ===== 跳空判斷 =====
    gap_signal = "-"
    today_low = low.iloc[-1]
    yesterday_high = high.iloc[-2]

    if (
        ENABLE_GAP_SIGNAL
        and pd.notna(today_low)
        and pd.notna(yesterday_high)
        and today_low > yesterday_high
    ):
        gap_signal = "跳空"

    return {
        "price": round(float(price), 2),
        "pct": round(float(change_pct), 2),
        "ma_range": ma_range,
        "ma_trend": ma_trend,
        "k": round(k_t, 1),
        "d": round(d_t, 1),
        "kd_signal": kd_signal,
        "gap_signal": gap_signal
    }


# ===== 顯示格式 =====
def format_color(val):
    if isinstance(val, (int, float)):
        if val > 0:
            return f"🔴 +{val:.2f}%"
        elif val < 0:
            return f"🟢 {val:.2f}%"
        else:
            return f"{val:.2f}%"
    return val


def format_k(val):
    if isinstance(val, (int, float)):
        if val >= 74:
            return f"🔴 {val:.1f}"
        elif val >= 50:
            return f"🟡 {val:.1f}"
        else:
            return f"🟢 {val:.1f}"
    return val


def format_gap(val):
    if val == "跳空":
        return "🔴 跳空"
    return "-"


# ===== 儀表板卡片 =====
def render_summary_dashboard(group_up_summary, rise_threshold):
    st.markdown("### 📌 漲幅儀表板")
    st.caption(f"目前儀表板統計門檻：漲幅 ≥ {rise_threshold}%")

    html_parts = []
    html_parts.append('<div class="dashboard-scroll"><div class="dashboard-grid">')

    for item in group_up_summary:
        group_name = escape(str(item["分類"]))
        anchor_id = make_anchor_id(group_name)

        hit_count = item["達標數"]
        total_count = item["總數"]
        up_count = item["上漲數"]
        down_count = item["下跌數"]
        hit_names_text = escape(str(item["達標股票名稱"]))
        top3_html = item["前三名HTML"]   # 不能 escape，否則顏色會失效

        hit_ratio = (hit_count / total_count * 100) if total_count > 0 else 0

        if hit_ratio >= 60:
            bg_color = "#fff1f0"
            border_color = "#ff7875"
            accent_color = "#cf1322"
        elif hit_ratio > 0:
            bg_color = "#fff7e6"
            border_color = "#ffa940"
            accent_color = "#d46b08"
        else:
            bg_color = "#f6ffed"
            border_color = "#95de64"
            accent_color = "#389e0d"

        card_html = (
            f'<a href="#{anchor_id}" class="dashboard-link">'
            f'<div class="dashboard-card" '
            f'style="background-color:{bg_color}; border:1px solid {border_color}; cursor:pointer;">'
            f'<div class="dashboard-title">{group_name}</div>'
            f'<div class="dashboard-main" style="color:{accent_color};">{hit_count} / {total_count}</div>'
            f'<div class="dashboard-sub">漲幅達標比例（≥{rise_threshold}%）：{hit_ratio:.0f}%</div>'
            f'<div class="dashboard-detail">'
            f'🎯 達標：<b>{hit_count}</b> 檔（{hit_names_text}）<br>'
            f'🔴 一般上漲：<b>{up_count}</b><br>'
            f'🟢 下跌：<b>{down_count}</b>'
            f'</div>'
            f'<div class="dashboard-extra">▶ {top3_html}</div>'
            f'</div>'
            f'</a>'
        )

        html_parts.append(card_html)

    html_parts.append("</div></div>")
    st.markdown("".join(html_parts), unsafe_allow_html=True)


# ==================== 主畫面開始 ====================
st.title("📊 股票監控面板 - 告訴我你會買日月光")
st.markdown('<div id="dashboard-top"></div>', unsafe_allow_html=True)

# ===== 手動更新與自動更新控制列 =====
col1, col2 = st.columns(2)

with col1:
    if st.button("🔄 手動更新即時資料 (清除快取)", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

with col2:
    auto_refresh = st.toggle(
        "⏱️ 啟用自動更新 (每 30 秒)",
        value=st.session_state.auto_refresh_enabled
    )

    if auto_refresh != st.session_state.auto_refresh_enabled:
        st.session_state.auto_refresh_enabled = auto_refresh
        st.rerun()

gc.collect()

# ===== 分組編輯鎖 =====
render_group_editor_lock()

if st.session_state.group_editor_unlocked:
    render_stock_group_editor()
else:
    st.sidebar.info("目前為唯讀模式：輸入 PIN 後才能修改股票分組")

# ===== 台灣時間 =====
tw_now = datetime.now(ZoneInfo("Asia/Taipei"))
st.caption(f"更新時間：{tw_now.strftime('%Y-%m-%d %H:%M:%S')}")

# ===== 儀表板門檻設定 =====
rise_threshold = st.slider(
    "儀表板漲幅達標門檻 (%)",
    min_value=5,
    max_value=9,
    value=5,
    step=1
)

# ===== 整理所有群組資料 =====
group_tables = {}
group_up_summary = []

for group_name, stocks in st.session_state.stock_groups.items():
    rows = []
    hit_count = 0
    up_count = 0
    down_count = 0
    flat_count = 0
    error_count = 0

    # 儀表板摘要需要的中間資料
    valid_stock_stats = []
    hit_names = []

    for symbol in stocks:
        try:
            raw_df = download_stock_data(symbol)
            df = normalize_ohlc(raw_df)

            if df.empty:
                raise ValueError("無法解析 yfinance 欄位格式")

            price = get_last_price(symbol, df)
            stock_name = get_stock_name(symbol)
            data = compute_indicators(df, price)

            if data["pct"] >= rise_threshold:
                hit_count += 1
                hit_names.append(stock_name)

            if data["pct"] > 0:
                up_count += 1
            elif data["pct"] < 0:
                down_count += 1
            else:
                flat_count += 1

            # 存給儀表板前三名用
            valid_stock_stats.append({
                "symbol": symbol,
                "code": symbol_to_code(symbol),
                "name": stock_name,
                "pct": float(data["pct"])
            })

            rows.append({
                "代碼": symbol,
                "代碼網址": yahoo_quote_url(symbol),
                "股票名稱": stock_name,
                "價格": f"{data['price']:.2f}",
                "漲跌%": data["pct"],
                "MA位置": data["ma_range"],
                "MA排列": data["ma_trend"],
                "K值": data["k"],
                "D值": f"{data['d']:.1f}",
                "KD訊號": data["kd_signal"],
                "跳空訊號": data["gap_signal"]
            })

        except Exception as e:
            error_count += 1
            rows.append({
                "代碼": symbol,
                "代碼網址": "",
                "股票名稱": get_stock_name(symbol),
                "價格": "錯誤",
                "漲跌%": "-",
                "MA位置": "-",
                "MA排列": "-",
                "K值": "-",
                "D值": "-",
                "KD訊號": "-",
                "跳空訊號": str(e)
            })

    # 整理儀表板顯示字串
    hit_names_text = compact_name_list(hit_names, max_show=4)
    top3_html = build_top3_html(valid_stock_stats)

    df_table = pd.DataFrame(rows)
    display_df = df_table.copy()

    if not display_df.empty:
        display_df["漲跌%"] = display_df["漲跌%"].apply(format_color)
        display_df["K值"] = display_df["K值"].apply(format_k)
        display_df["跳空訊號"] = display_df["跳空訊號"].apply(format_gap)

    group_tables[group_name] = {
        "count": len(stocks),
        "table": display_df
    }

    group_up_summary.append({
        "分類": group_name,
        "達標數": hit_count,
        "達標股票名稱": hit_names_text,
        "前三名HTML": top3_html,
        "上漲數": up_count,
        "下跌數": down_count,
        "平盤數": flat_count,
        "錯誤數": error_count,
        "總數": len(stocks)
    })

# ===== 顯示摘要與表格 =====
render_summary_dashboard(group_up_summary, rise_threshold)
st.divider()

for group_name, info in group_tables.items():
    anchor_id = make_anchor_id(group_name)
    st.markdown(
        f'<div id="{anchor_id}" style="scroll-margin-top: 80px;"></div>',
        unsafe_allow_html=True
    )

    header_col1, header_col2 = st.columns([8, 2])

    with header_col1:
        st.subheader(f"【{group_name}】({info['count']}檔)")

    with header_col2:
        st.markdown(
            """
            <div style="text-align:right; padding-top:0.4rem;">
                <a href="#dashboard-top" class="back-to-dashboard-btn">⬆ 回到儀表板</a>
            </div>
            """,
            unsafe_allow_html=True
        )

    table_df = info["table"].copy()

    if not table_df.empty and "代碼網址" in table_df.columns:
        table_df["代碼"] = table_df["代碼網址"]

    display_columns = [
        "代碼", "股票名稱", "價格", "漲跌%", "MA位置",
        "MA排列", "K值", "D值", "KD訊號", "跳空訊號"
    ]

    st.dataframe(
        table_df[display_columns],
        use_container_width=True,
        column_config={
            "代碼": st.column_config.LinkColumn(
                "代碼",
                help="點擊前往台股 Yahoo",
                display_text=r"https://tw\.stock\.yahoo\.com/quote/(.*)"
            ),
            "股票名稱": st.column_config.TextColumn("股票名稱")
        }
    )
    st.markdown('<div style="margin-bottom: 10px;"></div>', unsafe_allow_html=True)

# ===== 底部的自動刷新觸發判定 =====
# 為避免編輯中被重刷，只要分組編輯已解鎖 或 editing_mode=True 就暫停自動更新
if (
    st.session_state.auto_refresh_enabled
    and not st.session_state.group_editor_unlocked
    and not st.session_state.editing_mode
):
    time.sleep(REFRESH_SEC)
    st.rerun()
