import os
import re
import sys
import sqlite3
import shutil
import tempfile
import csv
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import urlparse


# ──────────────────────────────────────────────────────────────────────────────
# 论文相关 URL 识别
# ──────────────────────────────────────────────────────────────────────────────

# 已知学术/论文平台域名（部分匹配，只需包含即可）
_PAPER_DOMAINS: tuple[str, ...] = (
    # 预印本 / 开放获取
    "arxiv.org",
    "biorxiv.org",
    "medrxiv.org",
    "chemrxiv.org",
    "ssrn.com",
    "osf.io",
    "zenodo.org",
    # 搜索引擎 / 聚合
    "scholar.google",
    "semanticscholar.org",
    "researchgate.net",
    "academia.edu",
    "core.ac.uk",
    "unpaywall.org",
    "openalex.org",
    "dimensions.ai",
    "lens.org",
    # 数字图书馆 / 出版社
    "ieeexplore.ieee.org",
    "dl.acm.org",
    "link.springer.com",
    "springerlink.com",
    "sciencedirect.com",
    "onlinelibrary.wiley.com",
    "nature.com",
    "science.org",
    "cell.com",
    "tandfonline.com",
    "jstor.org",
    "pubmed.ncbi.nlm.nih.gov",
    "ncbi.nlm.nih.gov/pmc",
    "europepmc.org",
    "acs.org",
    "rsc.org",
    "degruyter.com",
    "karger.com",
    "hindawi.com",
    "mdpi.com",
    "frontiersin.org",
    "plos.org",
    "bmj.com",
    "thelancet.com",
    "nejm.org",
    "jamanetwork.com",
    "acpjournals.org",
    "oup.com",          # Oxford University Press
    "cambridge.org/core",
    "sagepub.com",
    "emerald.com",
    "informaworld.com",
    # DOI 解析
    "doi.org",
    "dx.doi.org",
    # 中文数据库
    "cnki.net",
    "wanfangdata.com",
    "cqvip.com",
    "oversea.cnki.net",
)

# URL 路径中含有这些片段视为论文页面（配合通用域名使用）
_PAPER_URL_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in [
        r"/abs/",          # arXiv abstract
        r"/pdf/",          # PDF 直链
        r"/paper/",
        r"/article/",
        r"/papers/",
        r"/publication",
        r"/fulltext",
        r"/full[-_]text",
        r"/abstract",
        r"/proceedings/",
        r"/conference/",
        r"10\.\d{4,}/",    # DOI 格式
    ]
)

# 标题中含有这些词视为论文（不区分大小写）
_PAPER_TITLE_KEYWORDS: tuple[str, ...] = (
    "arxiv", "abstract", "preprint",
    "journal", "proceedings", "conference",
    "ieee", "acm", "springer", "elsevier", "wiley",
    "pubmed", "doi:", "paper:", "论文", "期刊", "文献",
)


def _has_article_path(url: str) -> bool:
    """
    判断 URL 是否指向具体文章页面，而非网站首页或纯导航页。

    规则：解析后的路径去掉末尾斜杠，必须至少有一个长度 > 2 的路径段。
    这样可排除：
      https://arxiv.org              → path=""       → False
      https://arxiv.org/             → path="/"      → False
      https://arxiv.org/abs/2510.08  → path 有内容   → True
      https://doi.org/10.1234/xxx    → path 有内容   → True
    """
    try:
        path = urlparse(url).path.rstrip("/")
        segments = [s for s in path.split("/") if len(s) > 2]
        return bool(segments)
    except Exception:
        return True  # 解析失败时不过滤


def is_paper_url(url: str, title: str | None) -> bool:
    """
    判断一个 URL 是否与论文/学术文献相关。

    所有策略都要求 URL 必须指向具体文章页面（非首页/纯导航页），
    再叠加以下任意一条内容特征：
      1. URL 的域名部分命中已知学术平台列表
      2. URL 路径命中论文页面特征正则
      3. 页面标题含有学术关键词
    """
    # 前置检查：首页/纯导航页直接排除，不再进行内容判断
    if not _has_article_path(url):
        return False

    url_lower = (url or "").lower()
    title_lower = (title or "").lower()

    # 策略 1：域名匹配
    if any(domain in url_lower for domain in _PAPER_DOMAINS):
        return True

    # 策略 2：URL 路径特征匹配
    if any(pat.search(url_lower) for pat in _PAPER_URL_PATTERNS):
        return True

    # 策略 3：标题关键词匹配
    if any(kw in title_lower for kw in _PAPER_TITLE_KEYWORDS):
        return True

    return False


# ──────────────────────────────────────────────────────────────────────────────
# 今日时间过滤
# ──────────────────────────────────────────────────────────────────────────────

def is_today(unix_ts: float | None) -> bool:
    """判断 Unix 时间戳是否属于本地时间的今天。"""
    if unix_ts is None:
        return False
    try:
        visit_date = datetime.fromtimestamp(unix_ts).date()
        return visit_date == date.today()
    except (OSError, OverflowError, ValueError):
        return False


def unix_to_readable(unix_ts: float | None) -> str:
    """将 Unix 时间戳转为可读字符串，如 '2026-03-11 14:23:45'。"""
    if unix_ts is None:
        return ""
    try:
        return datetime.fromtimestamp(unix_ts).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return ""


def chrome_time_to_unix(ts: int) -> Optional[float]:
    """
    Chrome/Edge/Brave 的时间戳是:
    microseconds since 1601-01-01
    转成 Unix 时间戳
    """
    if not ts:
        return None
    try:
        return ts / 1_000_000 - 11644473600
    except Exception:
        return None


def firefox_time_to_unix(ts: int) -> Optional[float]:
    """
    Firefox 的 last_visit_date 通常是 microseconds since Unix epoch
    """
    if not ts:
        return None
    try:
        return ts / 1_000_000
    except Exception:
        return None


def safe_copy_db(src: Path) -> Optional[Path]:
    """
    复制数据库到临时目录，避免浏览器锁文件导致读取失败
    """
    if not src.exists():
        return None

    tmp_dir = Path(tempfile.mkdtemp())
    dst = tmp_dir / src.name
    try:
        shutil.copy2(src, dst)
        return dst
    except Exception as e:
        print(f"[WARN] copy db failed: {src} -> {e}")
        return None


def get_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    elif sys.platform.startswith("darwin"):
        return "mac"
    else:
        return "linux"


def get_chromium_history_paths() -> Dict[str, List[Path]]:
    """
    返回 Chrome / Edge / Brave 常见历史记录路径
    """
    platform = get_platform()
    home = Path.home()

    paths = {
        "chrome": [],
        "edge": [],
        "brave": [],
    }

    if platform == "windows":
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        appdata = Path(os.environ.get("APPDATA", ""))

        paths["chrome"] = [
            local / "Google/Chrome/User Data/Default/History",
            local / "Google/Chrome/User Data/Profile 1/History",
            local / "Google/Chrome/User Data/Profile 2/History",
        ]
        paths["edge"] = [
            local / "Microsoft/Edge/User Data/Default/History",
            local / "Microsoft/Edge/User Data/Profile 1/History",
            local / "Microsoft/Edge/User Data/Profile 2/History",
        ]
        paths["brave"] = [
            local / "BraveSoftware/Brave-Browser/User Data/Default/History",
            local / "BraveSoftware/Brave-Browser/User Data/Profile 1/History",
            local / "BraveSoftware/Brave-Browser/User Data/Profile 2/History",
        ]

    elif platform == "mac":
        paths["chrome"] = [
            home / "Library/Application Support/Google/Chrome/Default/History",
            home / "Library/Application Support/Google/Chrome/Profile 1/History",
            home / "Library/Application Support/Google/Chrome/Profile 2/History",
        ]
        paths["edge"] = [
            home / "Library/Application Support/Microsoft Edge/Default/History",
            home / "Library/Application Support/Microsoft Edge/Profile 1/History",
            home / "Library/Application Support/Microsoft Edge/Profile 2/History",
        ]
        paths["brave"] = [
            home / "Library/Application Support/BraveSoftware/Brave-Browser/Default/History",
            home / "Library/Application Support/BraveSoftware/Brave-Browser/Profile 1/History",
            home / "Library/Application Support/BraveSoftware/Brave-Browser/Profile 2/History",
        ]

    else:  # linux
        paths["chrome"] = [
            home / ".config/google-chrome/Default/History",
            home / ".config/google-chrome/Profile 1/History",
            home / ".config/google-chrome/Profile 2/History",
            home / ".config/chromium/Default/History",
        ]
        paths["edge"] = [
            home / ".config/microsoft-edge/Default/History",
            home / ".config/microsoft-edge/Profile 1/History",
            home / ".config/microsoft-edge/Profile 2/History",
        ]
        paths["brave"] = [
            home / ".config/BraveSoftware/Brave-Browser/Default/History",
            home / ".config/BraveSoftware/Brave-Browser/Profile 1/History",
            home / ".config/BraveSoftware/Brave-Browser/Profile 2/History",
        ]

    return paths


def get_firefox_history_paths() -> List[Path]:
    """
    Firefox 历史记录在 places.sqlite
    """
    platform = get_platform()
    home = Path.home()
    paths = []

    if platform == "windows":
        appdata = Path(os.environ.get("APPDATA", ""))
        base = appdata / "Mozilla/Firefox/Profiles"
    elif platform == "mac":
        base = home / "Library/Application Support/Firefox/Profiles"
    else:
        base = home / ".mozilla/firefox"

    if base.exists():
        for p in base.glob("*/places.sqlite"):
            paths.append(p)

    return paths


def read_chromium_history(db_path: Path, browser_name: str) -> List[Dict]:
    """
    读取 Chromium 系浏览器历史记录
    """
    results = []
    tmp_db = safe_copy_db(db_path)
    if not tmp_db:
        return results

    query = """
    SELECT
        urls.url,
        urls.title,
        urls.visit_count,
        urls.last_visit_time
    FROM urls
    ORDER BY urls.last_visit_time DESC
    """

    try:
        conn = sqlite3.connect(str(tmp_db))
        cur = conn.cursor()
        cur.execute(query)
        rows = cur.fetchall()
        conn.close()

        for row in rows:
            url, title, visit_count, last_visit_time = row
            results.append({
                "browser": browser_name,
                "db_path": str(db_path),
                "url": url,
                "title": title,
                "visit_count": visit_count,
                "last_visit_unix": chrome_time_to_unix(last_visit_time),
            })
    except Exception as e:
        print(f"[WARN] failed reading {browser_name}: {db_path} -> {e}")

    return results


def read_firefox_history(db_path: Path) -> List[Dict]:
    """
    读取 Firefox 历史记录
    """
    results = []
    tmp_db = safe_copy_db(db_path)
    if not tmp_db:
        return results

    query = """
    SELECT
        url,
        title,
        visit_count,
        last_visit_date
    FROM moz_places
    ORDER BY last_visit_date DESC
    """

    try:
        conn = sqlite3.connect(str(tmp_db))
        cur = conn.cursor()
        cur.execute(query)
        rows = cur.fetchall()
        conn.close()

        for row in rows:
            url, title, visit_count, last_visit_date = row
            results.append({
                "browser": "firefox",
                "db_path": str(db_path),
                "url": url,
                "title": title,
                "visit_count": visit_count,
                "last_visit_unix": firefox_time_to_unix(last_visit_date),
            })
    except Exception as e:
        print(f"[WARN] failed reading firefox: {db_path} -> {e}")

    return results


def collect_all_urls() -> List[Dict]:
    all_results = []

    chromium_paths = get_chromium_history_paths()
    for browser, paths in chromium_paths.items():
        for p in paths:
            if p.exists():
                all_results.extend(read_chromium_history(p, browser))

    firefox_paths = get_firefox_history_paths()
    for p in firefox_paths:
        all_results.extend(read_firefox_history(p))

    return all_results


def collect_today_paper_urls() -> List[Dict]:
    """
    收集用户当天访问过的所有论文/学术相关 URL。

    过滤条件（两者同时满足）：
      1. 访问时间属于本地时间的今天
      2. URL 或标题命中论文相关特征
    """
    all_records = collect_all_urls()
    filtered = [
        r for r in all_records
        if is_today(r.get("last_visit_unix"))
        and is_paper_url(r.get("url", ""), r.get("title"))
    ]
    # 去重（同一 URL 可能来自多个 Profile / 浏览器，保留 visit_count 最大的那条）
    seen: dict[str, Dict] = {}
    for r in filtered:
        url = r.get("url", "")
        if url not in seen or (r.get("visit_count") or 0) > (seen[url].get("visit_count") or 0):
            seen[url] = r
    return list(seen.values())


def save_to_csv(records: List[Dict], output_file: str = "all_browser_urls.csv"):
    if not records:
        print("[INFO] no records found")
        return

    fieldnames = ["browser", "db_path", "url", "title", "visit_count", "last_visit_time"]
    with open(output_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in records:
            row = dict(r)
            row["last_visit_time"] = unix_to_readable(r.get("last_visit_unix"))
            writer.writerow(row)

    print(f"[INFO] saved {len(records)} records to {output_file}")


def main():
    today_str = date.today().isoformat()

    print(f"[INFO] collecting today's ({today_str}) paper-related URLs ...")
    records = collect_today_paper_urls()
    print(f"[INFO] found {len(records)} paper-related URL(s) today")

    if records:
        # 按访问时间降序打印
        records_sorted = sorted(
            records,
            key=lambda r: r.get("last_visit_unix") or 0,
            reverse=True,
        )
        for i, r in enumerate(records_sorted, 1):
            time_str = unix_to_readable(r.get("last_visit_unix")) or "N/A"
            print(f"  {i:>3}. [{r['browser']}] {time_str}  {r['title'] or '(no title)'}")
            print(f"       {r['url']}")

    output_file = f"today_paper_urls_{today_str}.csv"
    save_to_csv(records, output_file)


if __name__ == "__main__":
    main()