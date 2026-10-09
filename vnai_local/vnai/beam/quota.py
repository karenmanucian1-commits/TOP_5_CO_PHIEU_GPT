import time
import functools
import threading
from collections import defaultdict
from datetime import datetime

def _package_version():
    try:
        from importlib.metadata import version
        return version("vnai")
    except Exception:
        return "unknown"

class RateLimitExceeded(Exception):
    def __init__(self, resource_type, limit_type="min", current_usage=None, limit_value=None, retry_after=None, tier=None, custom_message=None):
        self.resource_type = resource_type
        self.limit_type = limit_type
        self.current_usage = current_usage
        self.limit_value = limit_value
        self.retry_after = retry_after
        self.tier = tier
        if custom_message:
            message = f"\n{'='*60}\n"
            message += "⚠️  GIỚI HẠN API ĐÃ ĐẠT TỐI ĐA (Rate Limit Exceeded)\n"
            message += f"{'='*60}\n\n"
            message += f"{custom_message}\n"
            message += f"\n{'='*60}\n"
            super().__init__(message)
            return
        promotional_message = ""
        try:
            from vnai.scope.promo import (
                should_show_promotional_for_rate_limit,
                mark_promotional_shown,
                get_promotional_message
            )
            if should_show_promotional_for_rate_limit(tier):
                promotional_message = get_promotional_message() + "\n"
                mark_promotional_shown()
        except Exception:
            pass
        message = f"\n{'='*60}\n"
        message += "[Thông báo] ĐÃ HẾT HẠN MỨC LƯỢT GỌI (Rate Limit Exceeded)\n"
        message += f"{'='*60}\n\n"
        scope_names = {
            'min': 'phút (minute)',
            'hour': 'giờ (hour)',
            'day': 'ngày (day)',
            'month': 'tháng (month)'
        }
        scope_display = scope_names.get(limit_type, limit_type)
        message += f"Bạn đã dùng hết hạn mức lượt gọi trong 1 {scope_display}.\n"
        message += "   (You have reached the maximum request limit for this period)\n\n"
        message += "Chi tiết (Details):\n"
        if tier:
            tier_names = {
                'guest': 'Khách (Guest)',
                'free': 'Bản Cộng đồng (Community)',
                'bronze': 'Cấp Bronze (Bronze tier)',
                'silver': 'Cấp Silver (Silver tier)',
                'golden': 'Cấp Golden (Golden tier)',
                'diamond': 'Cấp Diamond (Diamond tier)'
            }
            tier_display = tier_names.get(tier, f"Cấp {tier.title()}")
            message += f"   • Cấp hiện tại: {tier_display}\n"
        message += f"   • Hạn mức: {limit_value} lượt gọi/{scope_display.split()[0]}\n"
        message += f"   • Đã sử dụng: {current_usage}/{limit_value}\n"
        if retry_after:
            message += f"   • Chờ {round(retry_after)} giây để tiếp tục (Wait to retry)\n"
        message += "\nGiải pháp (Solutions):\n"
        message += f"   1. Chờ {round(retry_after) if retry_after else 'khoảng 60'} giây rồi thử lại\n"
        message += "      (Wait and retry)\n"
        message += "   2. Hạn mức là đơn vị kỹ thuật để điều tiết tải ở mức hợp lý với nguồn,\n"
        message += "      không phải dữ liệu bán theo đơn vị. Hạn mức tự làm mới theo kỳ.\n"
        message += "   3. Tham gia chương trình Vnstock Sponsor để nhận hạn mức cao hơn và quyền\n"
        message += "      sử dụng các thư viện bản Mở rộng.\n"
        if tier == 'guest':
            message += "\nCấp tài trợ và đăng ký (Upgrade):\n"
            message += "   • Bản Cộng đồng (60 lượt gọi/phút, miễn phí cho cá nhân, học tập và nghiên cứu):\n"
            message += "     Đăng ký khoá API: https://vnstocks.com/login\n"
            message += "   • Chương trình Vnstock Sponsor (Bản Mở rộng 180-600 lượt gọi/phút):\n"
            message += "     Xem các cấp tài trợ: https://vnstocks.com/insiders-program\n"
            message += "     Hướng dẫn cài đặt Bản Mở rộng: https://vnstocks.com/onboard-member\n"
        elif tier == 'free':
            message += "\nCấp tài trợ và đăng ký (Upgrade):\n"
            message += "   • Chương trình Vnstock Sponsor (Bản Mở rộng 180-600 lượt gọi/phút):\n"
            message += "     Xem các cấp tài trợ: https://vnstocks.com/insiders-program\n"
        else:
            message += "\nCấp tài trợ và đăng ký (Upgrade):\n"
            message += "   • Xem các cấp tài trợ cao hơn: https://vnstocks.com/insiders-program\n"
        message += f"\n{'='*60}\n"
        if promotional_message:
            message += promotional_message
        super().__init__(message)

class Guardian:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(Guardian, cls).__new__(cls)
                cls._instance._initialize()
            return cls._instance

    def _initialize(self):
        self.usage_counters = defaultdict(lambda: defaultdict(list))
        self.quota_manager = None
        self._unsynced_count = 0
        self._last_sync = time.time()
        self._server_blocked_error = None
        import atexit
        atexit.register(self._flush_usage_on_exit)

    def _flush_usage_on_exit(self):
        if self._unsynced_count > 0:
            try:
                from vnai.beam.auth import authenticator
                if authenticator.has_active_subscription():
                    api_key = authenticator.get_api_key()
                    if api_key:
                        usage_to_sync = self._unsynced_count
                        self._unsynced_count = 0
                        self._sync_to_backend(api_key, usage_to_sync)
            except Exception:
                pass

    def _sync_to_backend(self, api_key, usage_count):
        try:
            import requests
            from vnai.scope.profile import inspector
            sys_info = inspector.examine()
            device_id = sys_info.get("machine_id", "unknown")
            payload = {
                'api_key': api_key,
                'device_id': device_id,
                'package_name': 'vnai',
                'version': _package_version(),
                'usage_count': usage_count
            }
            url = 'https://vnstocks.com/api/vnstock/license/verify'
            response = requests.post(url, json=payload, timeout=5)
            if response.status_code == 429:
                data = response.json()
                self._server_blocked_error = data.get('error', 'API Limit Exceeded (Backend)')
                if data.get('action'):
                    self._server_blocked_error += "\n\n👉 " + data['action']
            elif response.status_code == 200:
                self._server_blocked_error = None
        except Exception:
            pass

    def _get_quota_manager(self):
        if self.quota_manager is None:
            try:
                from vnai.beam.quota_manager import quota_manager
                self.quota_manager = quota_manager
            except ImportError:
                return None
        return self.quota_manager

    def _get_tier_limits(self):
        try:
            from vnai.beam.auth import authenticator
            return authenticator.get_limits()
        except Exception:
            return {"min": 20, "hour": 1200, "day": 5000}

    def _get_current_tier(self):
        try:
            from vnai.beam.auth import authenticator
            return authenticator.get_tier()
        except Exception:
            return None

    def verify(self, operation_id, resource_type="default", api_key=None):
        if not api_key:
            try:
                from vnai.beam.auth import authenticator
                if authenticator.has_active_subscription():
                    api_key = authenticator.get_api_key()
            except Exception:
                pass
        if getattr(self, '_server_blocked_error', None):
            current_time = time.time()
            time_since_sync = current_time - getattr(self, '_last_sync', 0)
            if api_key and time_since_sync > 300:
                self._last_sync = current_time
                threading.Thread(
                    target=self._sync_to_backend,
                    args=(api_key, 0),
                    daemon=True
                ).start()
            current_tier = self._get_current_tier()
            raise RateLimitExceeded(
                resource_type=resource_type,
                limit_type="backend_limit",
                current_usage="Backend",
                limit_value="Backend",
                retry_after=max(1, 300 - time_since_sync),
                tier=current_tier,
                custom_message=self._server_blocked_error
            )
        current_time = time.time()
        limits = self._get_tier_limits()
        if api_key:
            qm = self._get_quota_manager()
            if qm:
                quota_check = qm.check_quota(api_key)
                if not quota_check.get("allowed"):
                    raise RateLimitExceeded(
                        resource_type=resource_type,
                        limit_type=quota_check.get("reason", "unknown"),
                        current_usage=quota_check.get("usage"),
                        limit_value=quota_check.get("limit"),
                        retry_after=quota_check.get("reset_in_seconds")
                    )
        minute_cutoff = current_time - 60
        self.usage_counters[resource_type]["min"] = [
            t for t in self.usage_counters[resource_type]["min"]
            if t > minute_cutoff
        ]
        minute_usage = len(self.usage_counters[resource_type]["min"])
        minute_exceeded = minute_usage >= limits["min"]
        if minute_exceeded:
            from vnai.beam.metrics import collector
            collector.record(
                "rate_limit",
                {
                    "resource_type": resource_type,
                    "limit_type": "min",
                    "limit_value": limits["min"],
                    "current_usage": minute_usage,
                    "is_exceeded": True
                },
                priority="high"
            )
            current_tier = self._get_current_tier()
            raise RateLimitExceeded(
                resource_type=resource_type,
                limit_type="min",
                current_usage=minute_usage,
                limit_value=limits["min"],
                retry_after=60 - (current_time % 60),
                tier=current_tier
            )
        hour_cutoff = current_time - 3600
        self.usage_counters[resource_type]["hour"] = [
            t for t in self.usage_counters[resource_type]["hour"]
            if t > hour_cutoff
        ]
        hour_usage = len(self.usage_counters[resource_type]["hour"])
        hour_exceeded = hour_usage >= limits["hour"]
        from vnai.beam.metrics import collector
        collector.record(
            "rate_limit",
            {
                "resource_type": resource_type,
                "limit_type": "hour" if hour_exceeded else "min",
                "limit_value": limits["hour"] if hour_exceeded else limits["min"],
                "current_usage": hour_usage if hour_exceeded else minute_usage,
                "is_exceeded": hour_exceeded
            }
        )
        if hour_exceeded:
            current_tier = self._get_current_tier()
            raise RateLimitExceeded(
                resource_type=resource_type,
                limit_type="hour",
                current_usage=hour_usage,
                limit_value=limits["hour"],
                retry_after=3600 - (current_time % 3600),
                tier=current_tier
            )
        self.usage_counters[resource_type]["min"].append(current_time)
        self.usage_counters[resource_type]["hour"].append(current_time)
        self._unsynced_count += 1
        if api_key and (current_time - getattr(self, '_last_sync', 0) > 300 or getattr(self, '_unsynced_count', 0) >= 50):
            usage_to_sync = self._unsynced_count
            self._unsynced_count = 0
            self._last_sync = current_time
            if usage_to_sync > 0:
                threading.Thread(
                    target=self._sync_to_backend,
                    args=(api_key, usage_to_sync),
                    daemon=True
                ).start()
        return True

    def usage(self, resource_type="default"):
        current_time = time.time()
        limits = self._get_tier_limits()
        minute_cutoff = current_time - 60
        hour_cutoff = current_time - 3600
        self.usage_counters[resource_type]["min"] = [
            t for t in self.usage_counters[resource_type]["min"]
            if t > minute_cutoff
        ]
        self.usage_counters[resource_type]["hour"] = [
            t for t in self.usage_counters[resource_type]["hour"]
            if t > hour_cutoff
        ]
        minute_usage = len(self.usage_counters[resource_type]["min"])
        hour_usage = len(self.usage_counters[resource_type]["hour"])
        minute_percentage = (minute_usage / limits["min"]) * 100 if limits["min"] > 0 else 0
        hour_percentage = (hour_usage / limits["hour"]) * 100 if limits["hour"] > 0 else 0
        return max(minute_percentage, hour_percentage)

    def get_limit_status(self, resource_type="default"):
        current_time = time.time()
        limits = self._get_tier_limits()
        minute_cutoff = current_time - 60
        hour_cutoff = current_time - 3600
        minute_usage = len([t for t in self.usage_counters[resource_type]["min"] if t > minute_cutoff])
        hour_usage = len([t for t in self.usage_counters[resource_type]["hour"] if t > hour_cutoff])
        return {
            "resource_type": resource_type,
            "minute_limit": {
                "usage": minute_usage,
                "limit": limits["min"],
                "percentage": (minute_usage / limits["min"]) * 100 if limits["min"] > 0 else 0,
                "remaining": max(0, limits["min"] - minute_usage),
                "reset_in_seconds": 60 - (current_time % 60)
            },
            "hour_limit": {
                "usage": hour_usage,
                "limit": limits["hour"],
                "percentage": (hour_usage / limits["hour"]) * 100 if limits["hour"] > 0 else 0,
                "remaining": max(0, limits["hour"] - hour_usage),
                "reset_in_seconds": 3600 - (current_time % 3600)
            }
        }
guardian = Guardian()

class CleanErrorContext:
    _last_message_time = 0
    _message_cooldown = 5

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is RateLimitExceeded:
            current_time = time.time()
            if current_time - CleanErrorContext._last_message_time >= CleanErrorContext._message_cooldown:
                print(f"\n⚠️ {str(exc_val)}\n")
                CleanErrorContext._last_message_time = current_time
            import sys
            sys.exit(f"Rate limit exceeded. {str(exc_val)} Process terminated.")
            return False
        return False

def optimize(resource_type='default', loop_threshold=10, time_window=5, ad_cooldown=150, content_trigger_threshold=3,
            max_retries=2, backoff_factor=2, debug=False):
    if callable(resource_type):
        func = resource_type
        return _create_wrapper(func, 'default', loop_threshold, time_window, ad_cooldown, content_trigger_threshold,
                             max_retries, backoff_factor, debug)
    if loop_threshold < 2:
        raise ValueError(f"loop_threshold must be at least 2, got {loop_threshold}")
    if time_window <= 0:
        raise ValueError(f"time_window must be positive, got {time_window}")
    if content_trigger_threshold < 1:
        raise ValueError(f"content_trigger_threshold must be at least 1, got {content_trigger_threshold}")
    if max_retries < 0:
        raise ValueError(f"max_retries must be non-negative, got {max_retries}")
    if backoff_factor <= 0:
        raise ValueError(f"backoff_factor must be positive, got {backoff_factor}")

    def decorator(func):
        return _create_wrapper(func, resource_type, loop_threshold, time_window, ad_cooldown, content_trigger_threshold,
                             max_retries, backoff_factor, debug)
    return decorator

def _create_wrapper(func, resource_type, loop_threshold, time_window, ad_cooldown, content_trigger_threshold,
                   max_retries, backoff_factor, debug):
    call_history = []
    last_ad_time = 0
    consecutive_loop_detections = 0
    session_displayed = False
    session_start_time = time.time()
    session_timeout = 1800
    @functools.wraps(func)

    def wrapper(*args, **kwargs):
        nonlocal last_ad_time, consecutive_loop_detections, session_displayed, session_start_time
        current_time = time.time()
        content_triggered = False
        if current_time - session_start_time > session_timeout:
            session_displayed = False
            session_start_time = current_time
        retries = 0
        while True:
            call_history.append(current_time)
            while call_history and current_time - call_history[0] > time_window:
                call_history.pop(0)
            loop_detected = len(call_history) >= loop_threshold
            if debug and loop_detected:
                print(f"Đã phát hiện vòng lặp cho {func.__name__}: {len(call_history)} lần gọi trong {time_window}s")
            if loop_detected:
                consecutive_loop_detections += 1
                if debug:
                    print(f"Số lần phát hiện vòng lặp liên tiếp: {consecutive_loop_detections}/{content_trigger_threshold}")
            else:
                consecutive_loop_detections = 0
            should_show_content = (consecutive_loop_detections >= content_trigger_threshold) and \
                                 (current_time - last_ad_time >= ad_cooldown) and \
                                 not session_displayed
            if should_show_content:
                last_ad_time = current_time
                consecutive_loop_detections = 0
                content_triggered = True
                session_displayed = True
                if debug:
                    print(f"Đã kích hoạt nội dung cho {func.__name__}")
                try:
                    from vnai.scope.promo import manager
                    manager.present_content(context="loop")
                except ImportError:
                    print(f"Phát hiện vòng lặp: Hàm '{func.__name__}' đang được gọi trong một vòng lặp")
                except Exception as e:
                    if debug:
                        print(f"Lỗi khi hiển thị nội dung: {str(e)}")
            try:
                with CleanErrorContext():
                    guardian.verify(func.__name__, resource_type)
            except RateLimitExceeded as e:
                from vnai.beam.metrics import collector
                collector.record(
                    "error",
                    {
                        "function": func.__name__,
                        "error": str(e),
                        "context": "resource_verification",
                        "resource_type": resource_type,
                        "retry_attempt": retries
                    },
                    priority="high"
                )
                if not session_displayed:
                    try:
                        from vnai.scope.promo import manager
                        manager.present_content(context="loop")
                        session_displayed = True
                        last_ad_time = current_time
                    except Exception:
                        pass
                if retries < max_retries:
                    wait_time = backoff_factor ** retries
                    retries += 1
                    if hasattr(e, "retry_after") and e.retry_after:
                        wait_time = min(wait_time, e.retry_after)
                    if debug:
                        print(f"Đã đạt giới hạn tốc độ cho {func.__name__}, thử lại sau {wait_time} giây (lần thử {retries}/{max_retries})")
                    time.sleep(wait_time)
                    continue
                else:
                    raise
            start_time = time.time()
            success = False
            error = None
            try:
                result = func(*args, **kwargs)
                success = True
                return result
            except Exception as e:
                error = str(e)
                raise
            finally:
                execution_time = time.time() - start_time
                try:
                    from vnai.beam.metrics import collector
                    collector.record(
                        "function",
                        {
                            "function": func.__name__,
                            "resource_type": resource_type,
                            "execution_time": execution_time,
                            "success": success,
                            "error": error,
                            "in_loop": loop_detected,
                            "loop_depth": len(call_history),
                            "content_triggered": content_triggered,
                            "timestamp": datetime.now().isoformat(),
                            "retry_count": retries if retries > 0 else None
                        }
                    )
                    if content_triggered:
                        collector.record(
                            "ad_opportunity",
                            {
                                "function": func.__name__,
                                "resource_type": resource_type,
                                "call_frequency": len(call_history) / time_window,
                                "consecutive_loops": consecutive_loop_detections,
                                "timestamp": datetime.now().isoformat()
                            }
                        )
                except ImportError:
                    pass
            break
    return wrapper

def rate_limit_status(resource_type="default"):
    return guardian.get_limit_status(resource_type)
