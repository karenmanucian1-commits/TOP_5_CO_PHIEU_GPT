import logging
import requests
import json
from datetime import datetime, timedelta
import random
import threading
import time
import urllib.parse
from pathlib import Path
_vnii_check_attempted = False

class AdCategory:
    FREE = 0
    MANDATORY = 1
    ANNOUNCEMENT = 2
    REFERRAL = 3
    FEATURE = 4
    GUIDE = 5
    SURVEY = 6
    PROMOTION = 7
    SECURITY = 8
    MAINTENANCE = 9
    WARNING = 10
API_KEY_CHECKER_AVAILABLE = None
api_key_checker = None
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)
    logger.setLevel(logging.ERROR)
from html import escape as _escape
from html.parser import HTMLParser
import re as _re
import sys as _sys
_ALLOWED_TAGS = {
    "div", "span", "p", "br", "hr", "a", "b", "strong", "i", "em", "u", "small",
    "sup", "sub", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "img",
    "table", "thead", "tbody", "tr", "td", "th", "blockquote", "code", "pre",
    "center", "style", "link",
}
_VOID_TAGS = {"br", "hr", "img", "link"}
_DROP_WITH_CONTENT = {
    "script", "iframe", "frame", "frameset", "object", "embed", "applet", "svg",
    "math", "template", "noscript", "title", "xml", "textarea", "select",
}
_ALLOWED_ATTRS = {
    "class", "style", "href", "src", "alt", "title", "width", "height", "target",
    "align", "colspan", "rowspan",
}
_UNSAFE_CSS = _re.compile(r"expression\s*\(|javascript:|behavior\s*:|-moz-binding|@import", _re.I)
_CONTROL_CHARS = _re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_MAX_HTML = 100_000
_MAX_TEXT = 4_000

def _safe_url(value: str, allow_mailto: bool = False) -> bool:
    v = _CONTROL_CHARS.sub("", value or "").strip().lower()
    return v.startswith("https://") or v.startswith("http://") or (allow_mailto and v.startswith("mailto:"))

class _HtmlSanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self._skip_depth = 0
        self._in_style = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _DROP_WITH_CONTENT:
            if tag not in _VOID_TAGS:
                self._skip_depth += 1
            return
        if self._skip_depth or tag not in _ALLOWED_TAGS:
            return
        if tag == "link":
            attr = dict((k.lower(), v or "") for k, v in attrs)
            href = attr.get("href", "")
            if attr.get("rel", "").lower() == "stylesheet" and href.startswith("https://fonts.googleapis.com/"):
                self.out.append(f'<link rel="stylesheet" href="{_escape(href, quote=True)}">')
            return
        kept = []
        for name, value in attrs:
            name = name.lower()
            value = value or ""
            if name not in _ALLOWED_ATTRS:
                continue
            if name == "href" and not _safe_url(value, allow_mailto=True):
                continue
            if name == "src" and not _safe_url(value):
                continue
            if name == "style" and _UNSAFE_CSS.search(value):
                continue
            kept.append(f'{name}="{_escape(value, quote=True)}"')
        if tag == "a" and any(k.startswith("target=") for k in kept):
            kept.append('rel="noopener noreferrer"')
        self.out.append(f"<{tag}{(' ' + ' '.join(kept)) if kept else ''}>")
        if tag == "style":
            self._in_style = True

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _DROP_WITH_CONTENT:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth or tag not in _ALLOWED_TAGS or tag in _VOID_TAGS:
            return
        if tag == "style":
            self._in_style = False
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_style:
            if not _UNSAFE_CSS.search(data):
                self.out.append(data.replace("<", ""))
            return
        self.out.append(_escape(data, quote=False))

def sanitize_remote_html(raw: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        return ""
    parser = _HtmlSanitizer()
    try:
        parser.feed(raw[:_MAX_HTML])
        parser.close()
    except Exception:
        return ""
    return "".join(parser.out).strip()

def sanitize_remote_text(raw: str) -> str:
    if not isinstance(raw, str):
        return ""
    return _CONTROL_CHARS.sub("", raw)[:_MAX_TEXT].strip()

class ContentManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(ContentManager, cls).__new__(cls)
                cls._instance._initialize()
            return cls._instance

    def _initialize(self, debug=False):
        self.content_base_url = "https://hq.vnstocks.com/content-delivery"
        self.is_paid_user = None
        self.license_checked = False
        self._debug = debug
        global API_KEY_CHECKER_AVAILABLE, api_key_checker
        if API_KEY_CHECKER_AVAILABLE is None:
            try:
                from vnai.scope.lc_integration import api_key_checker as _checker
                api_key_checker = _checker
                API_KEY_CHECKER_AVAILABLE = True
            except ImportError:
                API_KEY_CHECKER_AVAILABLE = False
                api_key_checker = None
        self.last_display = 0
        self.display_interval = 24 * 3600
        self.content_base_url = "https://hq.vnstocks.com/content-delivery"
        self.target_url = "https://vnstocks.com/lp-khoa-hoc-python-chung-khoan"
        self.image_url = (
            "https://vnstocks.com/img/trang-chu-vnstock-python-api-phan-tich-giao-dich-chung-khoan.jpg"
        )
        self.vnstock_dir = Path.home() / ".vnstock"
        self.message_cache_file = self.vnstock_dir / "message_cache.json"
        self.message_cache = {}
        self._load_message_cache()
        self._start_periodic_display()

    def _load_message_cache(self) -> None:
        try:
            if self.message_cache_file.exists():
                with open(self.message_cache_file, 'r') as f:
                    self.message_cache = json.load(f)
            else:
                self.message_cache = {}
        except Exception as e:
            logger.debug(f"Failed to load message cache: {e}")
            self.message_cache = {}

    def _save_message_cache(self) -> None:
        try:
            self.vnstock_dir.mkdir(exist_ok=True)
            with open(self.message_cache_file, 'w') as f:
                json.dump(self.message_cache, f, indent=2)
        except Exception as e:
            logger.debug(f"Failed to save message cache: {e}")

    def should_show_promotional_for_rate_limit(self, tier: str) -> bool:
        if tier != "free":
            return False
        last_promo = self.message_cache.get("last_promotional_message")
        if last_promo:
            try:
                last_time = datetime.fromisoformat(last_promo)
                now = datetime.now()
                if (now - last_time).total_seconds() < 86400:
                    return False
            except Exception as e:
                logger.debug(f"Error parsing last promotional time: {e}")
        session_start = self.message_cache.get("session_start")
        if session_start:
            try:
                start_time = datetime.fromisoformat(session_start)
                now = datetime.now()
                if (now - start_time).total_seconds() < 300:
                    return False
            except Exception as e:
                logger.debug(f"Error parsing session start time: {e}")
        else:
            self.message_cache["session_start"] = datetime.now().isoformat()
            self._save_message_cache()
            return False
        return True

    def mark_promotional_shown(self) -> None:
        self.message_cache["last_promotional_message"] = datetime.now().isoformat()
        self._save_message_cache()

    def _check_license_status(self):
        if self.license_checked:
            return
        if API_KEY_CHECKER_AVAILABLE and api_key_checker:
            try:
                result = api_key_checker.check_license_with_vnii()
                if self._debug:
                    logger.info(f"API key check result: {result}")
                valid_statuses = [
                    'verified',
                    'cached',
                    'device_limit_exceeded'
                ]
                if result.get('status') in valid_statuses:
                    self.is_paid_user = result.get('is_paid', False)
                    self.license_checked = True
                    if self._debug:
                        status_msg = (
                            "Detected paid user"
                            if self.is_paid_user
                            else "Detected free user"
                        )
                        logger.info(f"{status_msg} via API key")
                else:
                    self.is_paid_user = False
                    self.license_checked = True
                    if self._debug:
                        logger.info(
                            f"No valid license: {result.get('status')}"
                        )
            except Exception as e:
                if self._debug:
                    logger.error(f"Error checking license via API key: {e}")
                self.is_paid_user = False
                self.license_checked = True
        else:
            if self._debug:
                logger.warning(
                    "API key checker not available. "
                    "Cannot determine paid user status."
                )
            self.is_paid_user = False
            self.license_checked = True

    def _has_active_subscription(self) -> bool:
        try:
            from vnai.beam.auth import authenticator
            return authenticator.has_active_subscription()
        except Exception as e:
            if self._debug:
                logger.error(f"Error checking subscription: {e}")
            return False

    def _get_startup_ad_content(self, tier: str) -> dict:
        content = {"html": "", "markdown": "", "terminal": "", "simple": ""}
        if tier == 'guest':
            content["html"] = """
            <div style="border: 2px solid #e67e22; padding: 15px; border-radius: 8px; margin: 10px 0; background: linear-gradient(135deg, #fef9e7, #fdebd0);">
                <h3 style="color: #e67e22; margin-top: 0;">Đăng ký khoá API để mở rộng hạn mức bản Cộng đồng</h3>
                <p>Bạn đang sử dụng <strong>chế độ khách</strong> với hạn mức:</p>
                <ul>
                    <li>20 requests/phút | Tối đa 4 kỳ báo cáo tài chính</li>
                </ul>
                <p><strong>Đăng ký khoá API bản Cộng đồng</strong> (miễn phí cho cá nhân, học tập và nghiên cứu) để nhận 60 requests/phút và 8 kỳ báo cáo:</p>
                <ol>
                    <li>Truy cập <a href="https://vnstocks.com/login" style="color: #2980b9;">vnstocks.com/login</a> để đăng ký tài khoản</li>
                    <li>Lấy khoá API tại <a href="https://vnstocks.com/account" style="color: #2980b9;">vnstocks.com/account</a></li>
                    <li>Chạy: <code>vnai.setup_api_key("your_api_key")</code></li>
                </ol>
                <p style="margin-bottom: 5px;">📚 Tài liệu: <a href="https://vnstocks.com/docs" style="color: #2980b9;">vnstocks.com/docs</a> | 
                Cộng đồng: <a href="https://facebook.com/groups/vnstock.official" style="color: #2980b9;">Facebook Group</a></p>
                <p style="margin-bottom: 0; color: #e67e22;"><strong>Chương trình Vnstock Sponsor (bản Mở rộng với hạn mức cao hơn):</strong> 
                <a href="https://vnstocks.com/insiders-program" style="color: #e67e22;">vnstocks.com/insiders-program</a></p>
            </div>
            """
            content["markdown"] = """
## Đăng ký khoá API để mở rộng hạn mức bản Cộng đồng

Bạn đang sử dụng **chế độ khách** (20 requests/phút | Tối đa 4 kỳ BCTC).

**Đăng ký khoá API bản Cộng đồng** (miễn phí cho cá nhân, học tập và nghiên cứu) để nhận 60 requests/phút và 8 kỳ báo cáo:
1. Truy cập [vnstocks.com/login](https://vnstocks.com/login) để đăng ký tài khoản
2. Lấy khoá API tại [vnstocks.com/account](https://vnstocks.com/account)
3. Chạy: `vnai.setup_api_key("your_api_key")`

📚 Tài liệu: [vnstocks.com/docs](https://vnstocks.com/docs) | Cộng đồng: [Facebook Group](https://facebook.com/groups/vnstock.official)

Chương trình Vnstock Sponsor (bản Mở rộng với hạn mức cao hơn): [vnstocks.com/insiders-program](https://vnstocks.com/insiders-program)
            """
            content["terminal"] = """
╔════════════════════════════════════════════════════════════╗
║                                                            ║
║  ĐĂNG KÝ KHOÁ API BẢN CỘNG ĐỒNG                            ║
║                                                            ║
║  Chế độ khách: 20 req/phút | Tối đa 4 kỳ BCTC              ║
║                                                            ║
║  bản Cộng đồng (60 req/phút | 8 kỳ BCTC):                  ║
║    1. Đăng ký: https://vnstocks.com/login                  ║
║    2. Lấy khoá: https://vnstocks.com/account               ║
║    3. Chạy: vnai.setup_api_key("your_api_key")             ║
║                                                            ║
║  📚 Tài liệu: https://vnstocks.com/docs                    ║
║  👥 Cộng đồng: https://facebook.com/groups/vnstock.official║
║                                                            ║
║  Chương trình Vnstock Sponsor (bản Mở rộng):               ║
║     https://vnstocks.com/insiders-program                  ║
║                                                            ║
╚════════════════════════════════════════════════════════════╝
            """
            content["simple"] = (
                "Đăng ký khoá API bản Cộng đồng: vnstocks.com/login → vnai.setup_api_key('key') "
                "| 📚 Tài liệu: vnstocks.com/docs "
                "| Chương trình Vnstock Sponsor: vnstocks.com/insiders-program"
            )
        elif tier == 'free':
            content["html"] = """
            <div style="border: 2px solid #3498db; padding: 15px; border-radius: 8px; margin: 10px 0; background: linear-gradient(135deg, #ebf5fb, #d6eaf8);">
                <h3 style="color: #3498db; margin-top: 0;">👋 Chào mừng bạn đến với Vnstock!</h3>
                <p>Bạn đang sử dụng <strong>bản Cộng đồng</strong>:</p>
                <ul>
                    <li>60 requests/phút | Tối đa 8 kỳ báo cáo tài chính</li>
                </ul>
                <p style="color: #2980b9;"><strong>⭐ Tham gia chương trình Vnstock Sponsor để:</strong></p>
                <ul>
                    <li>✓ Nhận hạn mức cao hơn (180-600 requests/phút)</li>
                    <li>✓ Truy xuất đầy đủ lịch sử báo cáo tài chính</li>
                    <li>✓ Sử dụng các thư viện bản Mở rộng <strong>vnstock_data</strong> với tính năng chuyên sâu</li>
                </ul>
                <p style="margin-bottom: 0;">➤ <a href="https://vnstocks.com/insiders-program" style="color: #2980b9; font-weight: bold;">Tìm hiểu chương trình: vnstocks.com/insiders-program</a></p>
                <p style="margin-top: 5px; font-size: 0.9em;">📚 <a href="https://vnstocks.com/docs" style="color: #3498db;">Tài liệu</a> | 
                👥 <a href="https://facebook.com/groups/vnstock.official" style="color: #3498db;">Cộng đồng</a></p>
            </div>
            """
            content["markdown"] = """
## 👋 Chào mừng bạn đến với Vnstock!

Bạn đang sử dụng **bản Cộng đồng** (60 requests/phút | Tối đa 8 kỳ BCTC).

⭐ **Tham gia chương trình Vnstock Sponsor để:**
* ✓ Nhận hạn mức cao hơn (180-600 requests/phút)
* ✓ Truy xuất đầy đủ lịch sử báo cáo tài chính
* ✓ Sử dụng các thư viện bản Mở rộng **vnstock_data** với tính năng chuyên sâu

➤ **Tìm hiểu chương trình:** [vnstocks.com/insiders-program](https://vnstocks.com/insiders-program)

📚 [Tài liệu](https://vnstocks.com/docs) | 👥 [Cộng đồng](https://facebook.com/groups/vnstock.official)
            """
            content["terminal"] = """
╔════════════════════════════════════════════════════════════╗
║                                                            ║
║  👋 Chào mừng bạn đến với Vnstock!                         ║
║                                                            ║
║  bản Cộng đồng: 60 req/phút | Tối đa 8 kỳ BCTC             ║
║                                                            ║
║  ⭐ Tham gia chương trình Vnstock Sponsor để:              ║
║    ✓ Nhận hạn mức cao hơn (180-600 req/phút)               ║
║    ✓ Truy xuất đầy đủ lịch sử báo cáo tài chính            ║
║    ✓ Sử dụng các thư viện bản Mở rộng vnstock_data         ║
║                                                            ║
║  ➤ Chi tiết: https://vnstocks.com/insiders-program         ║
║                                                            ║
║  📚 Tài liệu: https://vnstocks.com/docs                    ║
║  👥 Cộng đồng: https://facebook.com/groups/vnstock.official║
║                                                            ║
╚════════════════════════════════════════════════════════════╝
            """
            content["simple"] = (
                "👋 Chào mừng bạn đến với Vnstock! "
                "bản Cộng đồng (60 req/phút, tối đa 8 kỳ BCTC). "
                "⭐ Tham gia Vnstock Sponsor (bản Mở rộng 180-600 req/phút): vnstocks.com/insiders-program"
            )
        return content

    def show_startup_ad(self) -> bool:
        if self._has_active_subscription():
            if self._debug:
                logger.info("Skipping startup ad: user is a sponsor member")
            return False
        try:
            from vnai.beam.auth import authenticator
            tier = authenticator.get_tier()
        except Exception:
            tier = 'guest'
        if self._debug:
            logger.info(f"Showing startup ad for tier: {tier}")
        environment = 'unknown'
        try:
            from vnai.scope.profile import inspector
            environment = inspector.examine().get("environment", "unknown")
        except Exception as e:
            logger.debug(f"Environment detection failed: {e}")
        remote_content = self.fetch_remote_content(
            context="init", html=(environment == "jupyter")
        )
        fallback = self._get_startup_ad_content(tier)
        if environment == "jupyter":
            try:
                from IPython.display import display, HTML, Markdown
                if remote_content:
                    display(HTML(remote_content))
                else:
                    try:
                        display(Markdown(fallback["markdown"]))
                    except Exception:
                        display(HTML(fallback["html"]))
            except Exception:
                pass
        elif environment == "terminal":
            if remote_content:
                print(remote_content)
            else:
                print(fallback["terminal"])
        else:
            if remote_content:
                print(remote_content)
            else:
                print(fallback["simple"])
        self.last_display = time.time()
        return True

    def _start_periodic_display(self):
        def periodic_display():
            while True:
                if self._has_active_subscription():
                    break
                sleep_time = random.randint(2 * 3600, 6 * 3600)
                time.sleep(sleep_time)
                if self._has_active_subscription():
                    break
                current_time = time.time()
                if current_time - self.last_display >= self.display_interval:
                    self.present_content(context="periodic")
                else:
                    pass
        thread = threading.Thread(target=periodic_display, daemon=True)
        thread.start()

    def fetch_remote_content(self, context: str = "init", html: bool = True) -> str:
        if not self.license_checked:
            self._check_license_status()
        if self.is_paid_user:
            return ""
        if not html:
            try:
                if not _sys.stdout.isatty():
                    return None
            except Exception:
                return None
        try:
            params = {"context": context, "html": "true" if html else "false"}
            url = f"{self.content_base_url}?{urllib.parse.urlencode(params)}"
            response = requests.get(url, timeout=3)
            if response.status_code == 200:
                cleaned = (
                    sanitize_remote_html(response.text) if html
                    else sanitize_remote_text(response.text)
                )
                return cleaned or None
            logger.error(f"Non-200 response fetching content: {response.status_code}")
            return None
        except Exception as e:
            logger.error(f"Failed to fetch remote content: {e}")
            return None

    def present_content(self, context: str = "init", ad_category: int = AdCategory.FREE) -> None:
        if not self.license_checked:
            self._check_license_status()
        if self.is_paid_user and ad_category == AdCategory.FREE:
            return
        self.last_display = time.time()
        environment = None
        try:
            from vnai.scope.profile import inspector
            environment = inspector.examine().get("environment", "unknown")
        except Exception as e:
            logger.error(f"Không detect được environment: {e}")
            environment = "unknown"
        remote_content = self.fetch_remote_content(
            context=context, html=(environment == "jupyter")
        )
        fallback = self._generate_fallback_content(context)
        if environment == "jupyter":
            try:
                from IPython.display import display, HTML, Markdown
                if remote_content:
                    display(HTML(remote_content))
                else:
                    try:
                        display(Markdown(fallback["markdown"]))
                    except Exception:
                        display(HTML(fallback["html"]))
            except Exception:
                pass
        elif environment == "terminal":
            if remote_content:
                print(remote_content)
            else:
                print(fallback["terminal"])
        else:
            print(fallback["simple"])

    def _generate_fallback_content(self, context):
        fallback = {"html": "", "markdown": "", "terminal": "", "simple": ""}
        if context == "loop":
            fallback["html"] = (
                """
            <div style="border: 1px solid #e74c3c; padding: 15px; border-radius: 5px; margin: 10px 0;">
                <h3 style="color: #e74c3c;">⚠️ Phát hiện số lượng requests cao trong thời gian ngắn</h3>
                <p>Để tối ưu hiệu suất và tránh vượt hạn mức lượt gọi:</p>
                <ul>
                    <li>Thêm thời gian chờ (sleep) giữa các lần gọi dữ liệu</li>
                    <li>Sử dụng xử lý theo batch thay vì gọi liên tục trong vòng lặp</li>
                    <li>Tham gia chương trình <a href="https://vnstocks.com/insiders-program" style="color: #3498db;">Vnstock Sponsor</a> để nhận hạn mức cao hơn (180-600 requests/phút)</li>
                </ul>
            </div>
            """
            )
            fallback["markdown"] = (
                """
## ⚠️ Phát hiện số lượng requests cao trong thời gian ngắn

Để tối ưu hiệu suất và tránh vượt hạn mức lượt gọi:
* Thêm thời gian chờ (sleep) giữa các lần gọi dữ liệu
* Sử dụng xử lý theo batch thay vì gọi liên tục trong vòng lặp
* Tham gia chương trình [Vnstock Sponsor](https://vnstocks.com/insiders-program) để nhận hạn mức cao hơn (180-600 requests/phút)
            """
            )
            fallback["terminal"] = get_promotional_message("terminal")
            fallback["simple"] = get_promotional_message("simple")
        else:
            from vnai.beam.auth import authenticator
            tier = authenticator.get_tier()
            period_limit_info = ""
            if tier == 'guest':
                period_limit_info = (
                    "<p style='color: #2980b9; margin-top: 10px;'>"
                    "<strong>📊 bản Cộng đồng:</strong> Báo cáo tài chính được giới hạn tối đa <strong>4 kỳ</strong> để làm quen và kiểm thử thuật toán. "
                    "Để truy xuất đầy đủ lịch sử, vui lòng <a href='https://vnstocks.com/insiders-program' style='color: #2980b9;'>tìm hiểu chương trình Vnstock Sponsor</a>."
                    "</p>"
                )
            elif tier == 'free':
                period_limit_info = (
                    "<p style='color: #2980b9; margin-top: 10px;'>"
                    "<strong>📊 bản Cộng đồng:</strong> Báo cáo tài chính được giới hạn tối đa <strong>8 kỳ</strong> để làm quen và kiểm thử thuật toán. "
                    "Để truy xuất đầy đủ lịch sử, vui lòng <a href='https://vnstocks.com/insiders-program' style='color: #2980b9;'>tìm hiểu chương trình Vnstock Sponsor</a>."
                    "</p>"
                )
            fallback["html"] = (
                f"""
            <div style="border: 1px solid #3498db; padding: 15px; border-radius: 5px; margin: 10px 0;">
                <h3 style="color: #3498db;">👋 Chào mừng bạn đến với Vnstock!</h3>
                <p>Hệ sinh thái công cụ Python cho dữ liệu và nghiên cứu thị trường tài chính Việt Nam.</p>
                <ul>
                    <li>Tài liệu: <a href="https://vnstocks.com/docs" style="color: #3498db;">vnstocks.com/docs</a></li>
                    <li>Cộng đồng: <a href="https://facebook.com/groups/vnstock.official" style="color: #3498db;">vnstocks.com/community</a></li>
                </ul>
                <p>Khám phá các tính năng và tham gia cộng đồng để nhận hỗ trợ.</p>
                {period_limit_info}
            </div>
            """
            )
            period_limit_markdown = ""
            if tier == 'guest':
                period_limit_markdown = "\n**📊 bản Cộng đồng:** Báo cáo tài chính được giới hạn tối đa **4 kỳ** để làm quen và kiểm thử thuật toán. Để truy xuất đầy đủ lịch sử, vui lòng [tìm hiểu chương trình Vnstock Sponsor](https://vnstocks.com/insiders-program)."
            elif tier == 'free':
                period_limit_markdown = "\n**📊 bản Cộng đồng:** Báo cáo tài chính được giới hạn tối đa **8 kỳ** để làm quen và kiểm thử thuật toán. Để truy xuất đầy đủ lịch sử, vui lòng [tìm hiểu chương trình Vnstock Sponsor](https://vnstocks.com/insiders-program)."
            fallback["markdown"] = (
                f"""
## 👋 Chào mừng bạn đến với Vnstock!

Hệ sinh thái công cụ Python cho dữ liệu và nghiên cứu thị trường tài chính Việt Nam.

* Tài liệu: [Sổ tay hướng dẫn](https://vnstocks.com/docs)
* Cộng đồng: [Nhóm Facebook](https://facebook.com/groups/vnstock.official)

Khám phá các tính năng và tham gia cộng đồng để nhận hỗ trợ.{period_limit_markdown}
                """
            )
            period_limit_terminal = ""
            if tier == 'guest':
                period_limit_terminal = "\n║  📊 BCTC: Giới hạn 4 kỳ (bản Cộng đồng)                     ║\n║  Vnstock Sponsor: https://vnstocks.com/insiders-program     ║"
            elif tier == 'free':
                period_limit_terminal = "\n║  📊 BCTC: Giới hạn 8 kỳ (bản Cộng đồng)                     ║\n║  Vnstock Sponsor: https://vnstocks.com/insiders-program     ║"
            fallback["terminal"] = (
                f"""
╔════════════════════════════════════════════════════════════╗
║                                                            ║
║  👋 Chào mừng bạn đến với Vnstock!                         ║
║                                                            ║
║  Hệ sinh thái công cụ Python cho dữ liệu                   ║
║  và nghiên cứu thị trường tài chính Việt Nam               ║
║                                                            ║
║  ✓ Tài liệu: https://vnstocks.com/docs                     ║
║  ✓ Cộng đồng: https://facebook.com/groups/vnstock.official ║{period_limit_terminal}
║                                                            ║
║  Khám phá các tính năng và tham gia cộng đồng hỗ trợ.      ║
║                                                            ║
╚════════════════════════════════════════════════════════════╝
                """
            )
            period_limit_simple = ""
            if tier == 'guest':
                period_limit_simple = " | 📊 BCTC: Giới hạn 4 kỳ (bản Cộng đồng)"
            elif tier == 'free':
                period_limit_simple = " | 📊 BCTC: Giới hạn 8 kỳ (bản Cộng đồng)"
            fallback["simple"] = (
                "👋 Chào mừng bạn đến với Vnstock! "
                "Tài liệu: https://vnstocks.com/docs | "
                "Cộng đồng: https://facebook.com/groups/vnstock.official"
                f"{period_limit_simple}"
            )
        return fallback
manager = ContentManager()

def present(context: str = "init", ad_category: int = AdCategory.FREE) -> None:
    manager.present_content(context=context, ad_category=ad_category)

def get_promotional_message(format_type: str = "terminal") -> str:
    if format_type == "terminal":
        return """
╔═════════════════════════════════════════════════════════════════╗
║                                                                 ║
║   THÔNG TIN HẠN MỨC TRUY XUẤT Vnstock                           ║
║                                                                 ║
║   • bản Cộng đồng: Giữ tải an toàn (60 requests/phút)           ║
║   • bản Mở rộng: Hạn mức cao hơn từ 180 đến 600 requests/phút  ║
║                                                                 ║
║   Tìm hiểu chương trình Vnstock Sponsor:                        ║
║   https://vnstocks.com/insiders-program                         ║
║                                                                 ║
╚═════════════════════════════════════════════════════════════════╝
"""
    elif format_type == "simple":
        return (
            "Thông tin hạn mức: bản Cộng đồng giới hạn 60 requests/phút. "
            "Tham gia chương trình Vnstock Sponsor để nhận hạn mức cao hơn (180-600 requests/phút) "
            "và sử dụng bản Mở rộng: https://vnstocks.com/insiders-program"
        )
    elif format_type == "html":
        return """
<div style="border: 1px solid #3498db; padding: 15px; border-radius: 5px; margin: 10px 0; background-color: #ebf5fb;">
    <h3 style="color: #2980b9; margin-top: 0;">THÔNG TIN HẠN MỨC TRUY XUẤT Vnstock</h3>
    <p>• <strong>bản Cộng đồng:</strong> Giữ tải an toàn ở mức 60 requests/phút</p>
    <p>• <strong>bản Mở rộng:</strong> Hạn mức cao hơn từ 180 đến 600 requests/phút</p>
    <p style="margin-bottom: 0;">
        <a href="https://vnstocks.com/insiders-program" style="color: #2980b9; font-weight: bold;">
            ➤ Tìm hiểu chương trình Vnstock Sponsor: vnstocks.com/insiders-program
        </a>
    </p>
</div>
"""
    else:
        return get_promotional_message("terminal")

def should_show_promotional_for_rate_limit(tier: str) -> bool:
    return manager.should_show_promotional_for_rate_limit(tier)

def mark_promotional_shown() -> None:
    manager.mark_promotional_shown()
