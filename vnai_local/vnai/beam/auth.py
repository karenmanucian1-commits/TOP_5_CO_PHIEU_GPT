import os
import json
import time
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
import threading
log = logging.getLogger(__name__)

class AuthStateManager:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.state_file = state_dir / "auth_state.json"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._state = self._load_state()

    def _load_state(self) -> Dict:
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                log.debug(f"Could not load auth state: {e}")
                return self._default_state()
        return self._default_state()

    def _default_state(self) -> Dict:
        return {
            "session_id": self._generate_session_id(),
            "authenticated": False,
            "user": None,
            "tier": None,
            "auth_time": None,
            "packages_notified": [],
            "cache_ttl_minutes": 60
        }

    def _generate_session_id(self) -> str:
        return datetime.now().isoformat()

    def _save_state(self) -> None:
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self._state, f, indent=2)
        except IOError as e:
            log.warning(f"Could not save auth state: {e}")

    def _is_cache_valid(self) -> bool:
        if not self._state.get("auth_time"):
            return False
        auth_time = datetime.fromisoformat(self._state["auth_time"])
        ttl_minutes = self._state.get("cache_ttl_minutes", 60)
        expiry = auth_time + timedelta(minutes=ttl_minutes)
        return datetime.now() < expiry

    def should_show_message(self, package_name: str) -> bool:
        if not self._state.get("authenticated") or not self._is_cache_valid():
            return True
        packages_notified = self._state.get("packages_notified", [])
        return len(packages_notified) == 0

    def mark_authenticated(self, license_info: Dict, package_name: str) -> None:
        self._state["authenticated"] = True
        self._state["user"] = license_info.get("user", "Unknown")
        self._state["tier"] = license_info.get("tier", "free")
        self._state["auth_time"] = datetime.now().isoformat()
        packages_notified = self._state.get("packages_notified", [])
        if package_name not in packages_notified:
            packages_notified.append(package_name)
        self._state["packages_notified"] = packages_notified
        self._save_state()

    def get_cached_info(self) -> Optional[Dict]:
        if not self._is_cache_valid():
            return None
        return {
            "user": self._state.get("user"),
            "tier": self._state.get("tier"),
            "from_cache": True
        }

    def reset(self) -> None:
        self._state = self._default_state()
        self._save_state()

def get_auth_state_manager(project_dir: Path) -> AuthStateManager:
    return AuthStateManager(project_dir)

class Authenticator:
    TIER_LIMITS = {
        "guest": {"min": 20, "hour": 1200, "day": 5000},
        "free": {"min": 60, "hour": 3600, "day": 10000},
        "bronze": {"min": 180, "hour": 10800, "day": 50000},
        "silver": {"min": 300, "hour": 15000, "day": 100000},
        "golden": {"min": 500, "hour": 30000, "day": 150000},
        "diamond": {"min": 600, "hour": 36000, "day": 180000}
    }

    def __init__(self):
        self.vnstock_dir = Path.home() / ".vnstock"
        self.api_key_file = self.vnstock_dir / "api_key.json"
        self._cached_tier = None
        self._cache_timestamp = 0
        self._cache_ttl = 300

    def get_tier(self, force_refresh: bool = False) -> str:
        current_time = time.time()
        if not force_refresh and self._cached_tier and (current_time - self._cache_timestamp) < self._cache_ttl:
            return self._cached_tier
        tier = self._detect_tier()
        self._cached_tier = tier
        self._cache_timestamp = current_time
        log.debug(f"Detected tier: {tier}")
        return tier

    def _detect_tier(self) -> str:
        tier_from_vnii = self._check_vnii_tier()
        if tier_from_vnii:
            return tier_from_vnii
        if self._has_api_key():
            tier_from_server = self._check_server_tier()
            if tier_from_server:
                log.debug(f"Got tier from server: {tier_from_server}")
                return tier_from_server
            self._warn_tier_downgrade()
            return "free"
        return "guest"

    def _read_api_key(self) -> Optional[str]:
        env_key = os.getenv('VNSTOCK_API_KEY')
        if env_key and env_key.strip():
            return env_key.strip()
        if self.api_key_file.exists():
            try:
                with open(self.api_key_file, 'r') as f:
                    api_key = json.load(f).get('api_key', '').strip()
                    return api_key or None
            except Exception as e:
                log.debug(f"Failed to read API key: {e}")
        return None

    def _check_server_tier(self) -> Optional[str]:
        api_key = self._read_api_key()
        if not api_key:
            return None
        try:
            import requests
            from vnai.scope.profile import inspector
            device_id = (inspector.examine() or {}).get('machine_id') or 'vnai-tier-check'
            resp = requests.get(
                'https://vnstocks.com/api/vnstock/license/verify',
                params={'device_id': device_id},
                headers={'Authorization': f'Bearer {api_key}'},
                timeout=5,
            )
            if not resp.ok:
                log.debug(f"Server tier check returned HTTP {resp.status_code}")
                return None
            tier = str(((resp.json() or {}).get('subscription') or {}).get('tier') or '').strip().lower()
            return tier if tier in self.TIER_LIMITS else None
        except Exception as e:
            log.debug(f"Không hỏi được cấp từ máy chủ: {e}")
            return None

    def _warn_tier_downgrade(self) -> None:
        if getattr(self, '_downgrade_warned', False):
            return
        self._downgrade_warned = True
        log.warning(
            "Không xác định được gói tài trợ nên tạm áp hạn mức cấp Cộng đồng "
            "(60 lượt/phút). Nếu bạn đang tài trợ, nguyên nhân thường là thư viện `vnii` "
            "chưa cài được hoặc còn ở bản cũ — chạy "
            "`pip install -U --extra-index-url https://vnstocks.com/api/simple vnii` "
            "rồi thử lại."
        )

    def _check_vnii_tier(self) -> Optional[str]:
        try:
            import vnii
            from vnii.auth import authenticate
            license_info = authenticate(self.vnstock_dir)
            tier_string = license_info.get('tier', 'free')
            log.debug(f"Got tier from vnii: {tier_string}")
            return tier_string
        except ImportError:
            log.debug("vnii not installed")
            return None
        except SystemExit:
            log.debug("vnii authentication failed")
            return None
        except Exception as e:
            log.debug(f"Error checking vnii tier: {e}")
            return None

    def _has_api_key(self) -> bool:
        if os.getenv('VNSTOCK_API_KEY'):
            return True
        if self.api_key_file.exists():
            try:
                with open(self.api_key_file, 'r') as f:
                    data = json.load(f)
                    api_key = data.get('api_key', '').strip()
                    return bool(api_key)
            except Exception as e:
                log.debug(f"Failed to read API key: {e}")
        return False

    def get_limits(self, tier: Optional[str] = None) -> Dict[str, int]:
        if tier is None:
            tier = self.get_tier()
        return self.TIER_LIMITS.get(tier, self.TIER_LIMITS["guest"])

    def get_tier_info(self) -> Dict:
        tier = self.get_tier()
        limits = self.get_limits(tier)
        descriptions = {
            "guest": "Khách (Guest - chưa đăng ký)",
            "free": "Bản Cộng đồng (Community - có API key)",
            "bronze": "Cấp tài trợ Bronze (Bronze Sponsor)",
            "silver": "Cấp tài trợ Silver (Silver Sponsor)",
            "golden": "Cấp tài trợ Golden (Golden Sponsor)",
            "diamond": "Cấp tài trợ Diamond (Diamond Sponsor)",
        }
        return {
            "tier": tier,
            "description": descriptions.get(tier, f"Cấp {tier.title()}"),
            "limits": {
                "per_minute": limits["min"],
                "per_hour": limits["hour"]
            }
        }

    def setup_api_key(self, api_key: str) -> bool:
        try:
            self.vnstock_dir.mkdir(mode=0o700, exist_ok=True)
            api_key_data = {"api_key": api_key.strip()}
            fd = os.open(self.api_key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as f:
                json.dump(api_key_data, f, indent=2)
            try:
                os.chmod(self.api_key_file, 0o600)
            except OSError:
                pass
            self._cached_tier = None
            self._cache_timestamp = 0
            self._register_device_to_api(api_key.strip())
            print("✓ API key đã được lưu thành công! (API key saved successfully!)")
            print("Cấp tài khoản được kiểm với máy chủ khi bạn dùng thư viện.")
            print("(Your plan tier is checked with the server when you use the library.)")
            log.info("API key setup completed")
            return True
        except Exception as e:
            print(f"✗ Không thể lưu API key (Failed to save API key): {e}")
            log.error(f"API key setup failed: {e}")
            return False

    def get_api_key(self) -> Optional[str]:
        return self._read_api_key()

    def remove_api_key(self) -> bool:
        try:
            if self.api_key_file.exists():
                self.api_key_file.unlink()
                self._cached_tier = None
                self._cache_timestamp = 0
                print("✓ API key đã được xóa (API key removed)")
                print("Bạn đang ở chế độ Khách (20 requests/phút) (You are in Guest mode - 20 requests/minute)")
            else:
                print("Không tìm thấy API key (No API key found)")
            log.info("API key removed")
            return True
        except Exception as e:
            print(f"✗ Không thể xóa API key (Failed to remove API key): {e}")
            log.error(f"API key removal failed: {e}")
            return False

    def _register_device_to_api(self, api_key: str) -> None:
        try:
            import requests
            import platform
            from vnai.scope.profile import inspector
            from vnai.scope.device import IDEDetector
            system_info = inspector.examine()
            try:
                ide_name, ide_info = IDEDetector.detect_ide()
            except Exception:
                ide_name = 'Unknown'
                ide_info = {}
            payload = {
                'device_id': system_info['machine_id'],
                'device_name': system_info.get('platform', platform.node()),
                'os_type': system_info['os_name'].lower(),
                'os_version': system_info.get('platform', platform.release()),
                'machine_info': {
                    'platform': system_info.get('platform', platform.platform()),
                    'machine': platform.machine(),
                    'processor': platform.processor(),
                    'system': platform.system(),
                    'release': platform.release(),
                    'python_version': system_info.get('python_version'),
                    'environment': system_info.get('environment', 'unknown'),
                    'ide_name': ide_name,
                    'ide_detection_method': ide_info.get('detection_method'),
                    'ide_frontend': ide_info.get('frontend')
                }
            }
            url = 'https://vnstocks.com/api/vnstock/auth/device-register'
            try:
                requests.post(
                    url,
                    json=payload,
                    headers={'Authorization': f'Bearer {api_key}'},
                    timeout=5,
                )
                log.debug("Device registered successfully to vnstocks.com")
            except Exception as req_e:
                log.debug(f"Device registration request failed: {req_e}")
        except Exception as e:
            log.debug(f"Device registration error: {e}")

    def has_active_subscription(self) -> bool:
        tier = self.get_tier()
        paid_tiers = {'bronze', 'silver', 'golden', 'diamond'}
        return tier in paid_tiers

    def check_api_key_status(self) -> dict:
        api_key = self.get_api_key()
        if api_key:
            preview = f"{api_key[:4]}***{api_key[-4:]}" if len(api_key) > 12 else "***"
            tier_info = self.get_tier_info()
            print(f"✓ API key: {preview}")
            print(f"✓ Cấp tài trợ (Tier): {tier_info['tier']}")
            print(f"✓ Hạn mức (Limits): {tier_info['limits']}")
            return {
                'has_api_key': True,
                'api_key_preview': preview,
                'tier': tier_info['tier'],
                'limits': tier_info['limits']
            }
        else:
            return {
                'has_api_key': False,
                'api_key_preview': None,
                'tier': self.get_tier(),
                'limits': self.get_limits()
            }

    def print_help(self):
        print("\n" + "="*60)
        print("Vnstock - Cài đặt khoá API (API Key Setup)")
        print("="*60)
        print("\nCác cấp tài trợ và sử dụng (Tiers):")
        print("  • Khách (Guest): 20 lượt gọi/phút. Hỗ trợ tối đa 4 kỳ báo cáo tài chính.")
        print("  • Bản Cộng đồng (Community): 60 lượt gọi/phút. Hỗ trợ tối đa 8 kỳ báo cáo tài chính (miễn phí cho cá nhân, học tập và nghiên cứu).")
        print("  • Người tài trợ Vnstock Sponsor (Bản Mở rộng): 180-500 lượt gọi/phút.")
        print("  • Thay đổi câu lệnh từ 'from vnstock import ...' thành 'from vnstock_data import ...' để sử dụng Bản Mở rộng nếu đã tham gia tài trợ.")
        print("\nLấy khoá API (Get Your API Key):")
        print("  1. Truy cập: https://vnstocks.com/account")
        print("  2. Đăng ký hoặc đăng nhập (Sign up or log in)")
        print("  3. Sao chép khoá API của bạn (Copy your API key)")
        print("\nCài đặt khoá API (Setup API Key) - khoá được nhập ẩn:")
        print('  python -c "from vnstock import register_user; register_user()"')
        print("  Hoặc đặt biến môi trường VNSTOCK_API_KEY (or set the VNSTOCK_API_KEY variable)")
        print("\nKiểm tra trạng thái (Check Status):")
        print("  import vnai")
        print("  vnai.check_api_key_status()")
        print("\nTham gia chương trình Vnstock Sponsor:")
        print("  Truy cập: https://vnstocks.com/insiders-program")
        print("="*60 + "\n")
authenticator = Authenticator()
