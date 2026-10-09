from typing import Optional, Callable
from functools import wraps
import pandas as pd

def get_max_periods() -> Optional[int]:
    from vnai.beam.auth import authenticator
    PERIOD_LIMITS = {
        'guest': 4,
        'free': 8,
        'bronze': None,
        'silver': None,
        'golden': None,
    }
    tier = authenticator.get_tier()
    return PERIOD_LIMITS.get(tier)

def limit_vci_periods(df: pd.DataFrame, max_periods: Optional[int] = None) -> pd.DataFrame:
    if max_periods is None or df.empty:
        return df
    period_cols = []
    for col in df.columns:
        if isinstance(col, str) and (col.isdigit() or (len(col) > 4 and '-Q' in col)):
            if col not in ['row_number', 'item_id']:
                period_cols.append(col)
    if period_cols:
        return limit_periods_by_columns(df, max_periods=max_periods)
    if 'yearReport' in df.columns:
        sort_cols = ['yearReport']
        if 'lengthReport' in df.columns:
            sort_cols.append('lengthReport')
        df_sorted = df.sort_values(by=sort_cols, ascending=False)
        df_limited = df_sorted.head(max_periods).copy()
        df_limited = df_limited.reset_index(drop=True)
        return df_limited
    return df

def get_period_limit_notice() -> Optional[str]:
    from vnai.beam.auth import authenticator
    tier = authenticator.get_tier()
    if tier == 'guest':
        return (
            "Bản Cộng đồng: Dữ liệu báo cáo tài chính hiển thị tối đa 4 kỳ. "
            "Tham gia chương trình Vnstock Sponsor để nhận hạn mức cao hơn và quyền sử dụng bản Mở rộng: https://vnstocks.com/insiders-program\n"
            "Community edition: Financial statements limited to 4 periods. "
            "Sponsor Vnstock to use the extended edition with higher limits: https://vnstocks.com/insiders-program"
        )
    elif tier == 'free':
        return (
            "Bản Cộng đồng: Dữ liệu báo cáo tài chính hiển thị tối đa 8 kỳ. "
            "Tham gia chương trình Vnstock Sponsor để nhận hạn mức cao hơn và quyền sử dụng bản Mở rộng: https://vnstocks.com/insiders-program\n"
            "Community edition: Financial statements limited to 8 periods. "
            "Sponsor Vnstock to use the extended edition with higher limits: https://vnstocks.com/insiders-program"
        )
    return None

def get_period_limit_notice_html() -> Optional[str]:
    from vnai.beam.auth import authenticator
    tier = authenticator.get_tier()
    if tier == 'guest':
        return (
            "<div style='background-color: #e3f2fd; border-left: 4px solid #2196f3; "
            "padding: 12px 16px; margin: 12px 0; border-radius: 4px; font-size: 13px;'>"
            "<strong>Bản Cộng đồng (Community edition)</strong><br>"
            "Dữ liệu báo cáo tài chính hiển thị tối đa <strong>4 kỳ</strong>. "
            "Tham gia chương trình Vnstock Sponsor để nhận hạn mức cao hơn và quyền sử dụng bản Mở rộng: "
            "<a href='https://vnstocks.com/insiders-program' target='_blank'>https://vnstocks.com/insiders-program</a><br>"
            "<span style='color: #666;'><i>Financial statements limited to 4 periods. Sponsor Vnstock to use the extended edition with higher limits: "
            "<a href='https://vnstocks.com/insiders-program' target='_blank'>https://vnstocks.com/insiders-program</a></i></span>"
            "</div>"
        )
    elif tier == 'free':
        return (
            "<div style='background-color: #e3f2fd; border-left: 4px solid #2196f3; "
            "padding: 12px 16px; margin: 12px 0; border-radius: 4px; font-size: 13px;'>"
            "<strong>Bản Cộng đồng (Community edition)</strong><br>"
            "Dữ liệu báo cáo tài chính hiển thị tối đa <strong>8 kỳ</strong>. "
            "Tham gia chương trình Vnstock Sponsor để nhận hạn mức cao hơn và quyền sử dụng bản Mở rộng: "
            "<a href='https://vnstocks.com/insiders-program' target='_blank'>https://vnstocks.com/insiders-program</a><br>"
            "<span style='color: #666;'><i>Financial statements limited to 8 periods. Sponsor Vnstock to use the extended edition with higher limits: "
            "<a href='https://vnstocks.com/insiders-program' target='_blank'>https://vnstocks.com/insiders-program</a></i></span>"
            "</div>"
        )
    return None

def display_period_limit_notice_jupyter() -> None:
    try:
        from IPython.display import HTML, display
        notice_html = get_period_limit_notice_html()
        if notice_html:
            display(HTML(notice_html))
    except ImportError:
        notice = get_period_limit_notice()
        if notice:
            print(f"\n{notice}\n")

def should_show_notice() -> bool:
    from vnai.beam.auth import authenticator
    tier = authenticator.get_tier()
    return tier in ('guest', 'free')

def limit_periods_by_columns(df: pd.DataFrame, max_periods: Optional[int] = None) -> pd.DataFrame:
    if max_periods is None:
        return df
    metadata_cols = [
        'ticker', 'yearReport', 'lengthReport',
        'item', 'item_en', 'item_id', 'unit', 'levels', 'row_number'
    ]
    period_cols = []
    for col in df.columns:
        if isinstance(col, str) and col not in metadata_cols:
            if col.isdigit() or (len(col) > 4 and '-Q' in col):
                period_cols.append(col)
    if not period_cols:
        return df

    def parse_period(p):
        if '-Q' in str(p):
            year, quarter = str(p).split('-Q')
            return (int(year), int(quarter))
        else:
            return (int(p), 5)
    period_cols_sorted = sorted(period_cols, key=parse_period, reverse=True)
    keep_periods = period_cols_sorted[:max_periods]
    metadata_cols_present = [col for col in metadata_cols if col in df.columns]
    financial_cols = [col for col in df.columns if col not in metadata_cols and col not in period_cols]
    final_cols = metadata_cols_present + financial_cols + keep_periods
    return df[final_cols]

def patch_vci_finance():
    try:
        import sys
        try:
            from vnstock.explorer.vci.financial import Finance as VCI_Finance
        except ImportError:
            return False
        original_balance_sheet = VCI_Finance.balance_sheet
        original_income_statement = VCI_Finance.income_statement
        original_cash_flow = VCI_Finance.cash_flow
        _notice_shown = {'balance_sheet': False, 'income_statement': False, 'cash_flow': False}
        @wraps(original_balance_sheet)

        def balance_sheet_with_limit(self, period: Optional[str] = None, lang: Optional[str] = 'en',
                                     dropna: Optional[bool] = True, show_log: Optional[bool] = False) -> pd.DataFrame:
            max_p = get_max_periods()
            fetch_limit = 100
            df = self._get_financial_report('balance_sheet', period=period, lang=lang,
                                          dropna=dropna, show_log=show_log, limit=fetch_limit)
            df_limited = limit_vci_periods(df, max_periods=max_p)
            if should_show_notice() and not _notice_shown['balance_sheet']:
                _notice_shown['balance_sheet'] = True
                display_period_limit_notice_jupyter()
            return df_limited
        @wraps(original_income_statement)

        def income_statement_with_limit(self, period: Optional[str] = None, lang: Optional[str] = 'en',
                                       dropna: Optional[bool] = True, show_log: Optional[bool] = False) -> pd.DataFrame:
            max_p = get_max_periods()
            fetch_limit = 100
            df = self._get_financial_report('income_statement', period=period, lang=lang,
                                          dropna=dropna, show_log=show_log, limit=fetch_limit)
            df_limited = limit_vci_periods(df, max_periods=max_p)
            if should_show_notice() and not _notice_shown['income_statement']:
                _notice_shown['income_statement'] = True
                display_period_limit_notice_jupyter()
            return df_limited
        @wraps(original_cash_flow)

        def cash_flow_with_limit(self, period: Optional[str] = None, lang: Optional[str] = 'en',
                                dropna: Optional[bool] = True, show_log: Optional[bool] = False) -> pd.DataFrame:
            max_p = get_max_periods()
            fetch_limit = 100
            df = self._get_financial_report('cash_flow', period=period, lang=lang,
                                          dropna=dropna, show_log=show_log, limit=fetch_limit)
            df_limited = limit_vci_periods(df, max_periods=max_p)
            if should_show_notice() and not _notice_shown['cash_flow']:
                _notice_shown['cash_flow'] = True
                display_period_limit_notice_jupyter()
            return df_limited
        VCI_Finance.balance_sheet = balance_sheet_with_limit
        VCI_Finance.income_statement = income_statement_with_limit
        VCI_Finance.cash_flow = cash_flow_with_limit
        return True
    except Exception as e:
        print(f"Warning: Could not patch VCI Finance: {e}")
        return False

def patch_kbs_finance():
    try:
        try:
            from vnstock.explorer.kbs.financial import Finance as KBS_Finance
        except ImportError:
            return False
        from vnai.beam.fundamental import limit_periods_by_columns
        original_balance_sheet = KBS_Finance.balance_sheet
        original_income_statement = KBS_Finance.income_statement
        original_cash_flow = KBS_Finance.cash_flow
        _notice_shown = {'balance_sheet': False, 'income_statement': False, 'cash_flow': False}
        @wraps(original_balance_sheet)

        def balance_sheet_with_limit(self, period: Optional[str] = None, show_log: Optional[bool] = False) -> pd.DataFrame:
            df = original_balance_sheet(self, period=period, show_log=show_log)
            df_limited = limit_periods_by_columns(df, max_periods=get_max_periods())
            if should_show_notice() and not _notice_shown['balance_sheet']:
                _notice_shown['balance_sheet'] = True
                display_period_limit_notice_jupyter()
            return df_limited
        @wraps(original_income_statement)

        def income_statement_with_limit(self, period: Optional[str] = None, show_log: Optional[bool] = False) -> pd.DataFrame:
            df = original_income_statement(self, period=period, show_log=show_log)
            df_limited = limit_periods_by_columns(df, max_periods=get_max_periods())
            if should_show_notice() and not _notice_shown['income_statement']:
                _notice_shown['income_statement'] = True
                display_period_limit_notice_jupyter()
            return df_limited
        @wraps(original_cash_flow)

        def cash_flow_with_limit(self, period: Optional[str] = None, show_log: Optional[bool] = False) -> pd.DataFrame:
            df = original_cash_flow(self, period=period, show_log=show_log)
            df_limited = limit_periods_by_columns(df, max_periods=get_max_periods())
            if should_show_notice() and not _notice_shown['cash_flow']:
                _notice_shown['cash_flow'] = True
                display_period_limit_notice_jupyter()
            return df_limited
        KBS_Finance.balance_sheet = balance_sheet_with_limit
        KBS_Finance.income_statement = income_statement_with_limit
        KBS_Finance.cash_flow = cash_flow_with_limit
        return True
    except Exception as e:
        print(f"Warning: Could not patch KBS Finance: {e}")
        return False

def limit_ohlcv_periods(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    if df is None or df.empty or 'time' not in df.columns:
        return df
    interval_str = str(interval)
    if interval_str == "1m":
        max_days = 180
    elif interval_str in ["5m", "15m", "30m", "1H", "H", "1h", "1H"]:
        max_days = 365
    else:
        max_days = 8 * 365
    time_col = pd.to_datetime(df['time'])
    max_time = time_col.max()
    if pd.isna(max_time):
        return df
    cutoff_time = max_time - pd.Timedelta(days=max_days)
    df_limited = df[time_col >= cutoff_time].reset_index(drop=True)
    return df_limited

def patch_quote_history():
    try:
        from vnstock.api.quote import Quote
        original_history = Quote.history
        original_intraday = Quote.intraday
        _notice_shown = {'quote_history': False, 'quote_intraday': False}
        @wraps(original_history)

        def history_with_limit(*args, **kwargs):
            df = original_history(*args, **kwargs)
            if isinstance(df, pd.DataFrame) and not df.empty:
                actual_interval = kwargs.get("interval", "1D")
                if "resolution" in kwargs:
                    actual_interval = kwargs["resolution"]
                df_limited = limit_ohlcv_periods(df, actual_interval)
                if len(df_limited) < len(df) and not _notice_shown['quote_history']:
                    _notice_shown['quote_history'] = True
                    from vnstock.core.utils.logger import get_logger
                    logger = get_logger(__name__)
                    interval_str = str(actual_interval)
                    if interval_str == "1m":
                        limit_text = "6 tháng"
                        limit_text_en = "6 months"
                    elif interval_str in ["5m", "15m", "30m", "1H", "H", "1h"]:
                        limit_text = "1 năm"
                        limit_text_en = "1 year"
                    else:
                        limit_text = "8 năm"
                        limit_text_en = "8 years"
                    logger.warning(
                        f"Bản Cộng đồng: Dữ liệu OHLCV ({actual_interval}) hiển thị tối đa {limit_text}. "
                        f"Tham gia chương trình Vnstock Sponsor để nhận hạn mức cao hơn và quyền sử dụng bản Mở rộng: https://vnstocks.com/insiders-program\n"
                        f"Community edition: OHLCV data ({actual_interval}) limited to {limit_text_en}. "
                        f"Sponsor Vnstock to use the extended edition with higher limits: https://vnstocks.com/insiders-program"
                    )
                return df_limited
            return df
        @wraps(original_intraday)

        def intraday_with_limit(*args, **kwargs):
            df = original_intraday(*args, **kwargs)
            if isinstance(df, pd.DataFrame) and not df.empty:
                max_rows = 30000
                if len(df) > max_rows:
                    df_limited = df.head(max_rows).copy()
                    if not _notice_shown['quote_intraday']:
                        _notice_shown['quote_intraday'] = True
                        from vnstock.core.utils.logger import get_logger
                        logger = get_logger(__name__)
                        logger.warning(
                            "Bản Cộng đồng: Dữ liệu khớp lệnh hiển thị tối đa 30,000 dòng. "
                            "Tham gia chương trình Vnstock Sponsor để nhận hạn mức cao hơn và quyền sử dụng bản Mở rộng: https://vnstocks.com/insiders-program\n"
                            "Community edition: Intraday data limited to 30,000 rows. "
                            "Sponsor Vnstock to use the extended edition with higher limits: https://vnstocks.com/insiders-program"
                        )
                    return df_limited
            return df
        Quote.history = history_with_limit
        Quote.ohlcv = history_with_limit
        Quote.intraday = intraday_with_limit
        return True
    except Exception as e:
        print(f"Warning: Could not patch Quote history: {e}")
        return False
_patches_applied = False
_patches_lock = __import__('threading').Lock()

def apply_all_patches():
    global _patches_applied
    with _patches_lock:
        try:
            vci_patched = patch_vci_finance()
            kbs_patched = patch_kbs_finance()
            quote_patched = patch_quote_history()
            _patches_applied = True
            return {
                'vci': vci_patched,
                'kbs': kbs_patched,
                'quote': quote_patched,
            }
        except Exception:
            _patches_applied = True
            return {'vci': False, 'kbs': False}
