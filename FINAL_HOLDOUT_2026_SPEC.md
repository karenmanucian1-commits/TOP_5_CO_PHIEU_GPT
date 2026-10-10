# FINAL HOLDOUT 2026 --- SPEC v1.0 (LOCKED BEFORE RESULTS)

**Project:** TOP5_AI / Frozen V2\
**Purpose:** One-time untouched 2026 holdout test.\
**Rule:** No threshold, model, feature, execution rule, benchmark
definition, or PASS/FAIL criterion may be changed after viewing the 2026
result.

## 1. Frozen components

-   Frozen V2 model and its 34 features: unchanged.
-   OOS ranking convention: unchanged.
-   Telegram PRO price structure / Buy Zone / Stop / T1 / T2: unchanged.
-   Decision Layer thresholds and scoring: unchanged.
-   No retraining on 2026.
-   No parameter search using 2026.
-   No market-regime rule discovered from 2024--2025 may be added.

## 2. Common execution assumptions

For fair comparison, all strategies use the same raw OHLCV trading
calendar.

-   Buy fee: 0.15%.
-   Sell fee: 0.15%.
-   Sell tax: 0.10%.
-   Base slippage: 0.10% each side.
-   Sensitivity only: 0.20% and 0.30% each side.
-   No margin.
-   Portfolio allocation: 10% of initial NAV per new position.
-   Maximum simultaneous positions: 5.
-   Portfolio equity is marked-to-market every trading session.
-   Drawdown is calculated from the daily marked-to-market equity curve.
-   T+ settlement is handled conservatively. Intraday stop/target
    execution is not allowed before the first fully sellable daily bar.
-   No favorable assumption when daily OHLC cannot establish event
    ordering.
-   Same-bar stop and target: stop has priority.
-   Gap through stop: execute at the available opening price, not the
    theoretical stop.
-   Target gap: do not award a better price than the target.
-   All outcomes must be reported both before and after costs where
    applicable.

## 3. Strategy A --- Benchmark

A is the reference portfolio and must use the same execution clock and
transaction-cost convention as B.

Primary benchmark: - Equal-weight investable universe reference on each
signal date. - Entry: next trading-session Open. - Exit: Close of the
20th holding session using the same holding-session convention as B. -
Apply the same buy/sell fees, tax and slippage.

Also report VN-Index / broad-market reference if suitable raw benchmark
data is available, but it is secondary and cannot replace the primary
like-for-like A comparison.

## 4. Strategy B --- Frozen V2 Top5

-   Use the frozen OOS/Frozen-V2 ranking only.
-   Top 5 per signal date.
-   No Decision Layer filtering.
-   Entry: next trading-session Open.
-   Exit: Close of holding session 20.
-   Same costs as A.
-   Report:
    1.  B1 --- all Top5 signals independently.
    2.  B2 --- executable portfolio with 10% initial NAV per position
        and maximum 5 simultaneous positions.

## 5. Strategy C --- Frozen Decision Layer

-   Start only from that day's Frozen V2 Top5.
-   Apply the existing Decision Layer exactly as frozen.
-   At most one preferred candidate per signal day.
-   Do not add a future volume-confirmation condition.
-   Entry/exit for C: same next-session Open / holding-session-20 Close
    convention as B.
-   Report all Decision-selected signals separately from
    portfolio-executed signals.

## 6. Strategy D --- Telegram execution strategy

-   Candidate selection is exactly C.
-   Pending Buy Zone validity: maximum 3 trading sessions after signal.
-   If Open \> BuyHigh and the session trades down through BuyHigh: fill
    at BuyHigh.
-   If BuyLow \<= Open \<= BuyHigh: fill at Open.
-   If Open \< BuyLow and the bar later reaches the Buy Zone: label
    AMBIGUOUS.
    -   Main test: no trade.
    -   Sensitivity: fill at Open.
-   If price never reaches the Buy Zone within 3 sessions: EXPIRED.
-   T1: sell 50% when legally executable.
-   After T1: remaining stop moves to raw entry price (breakeven before
    costs).
-   T2: sell remaining 50%.
-   Same bar after first reaching T1: do not also award T2 to the
    remaining half.
-   Stop/target same bar: stop first.
-   Maximum holding period: 20 trading sessions from actual entry, with
    entry day counted as holding session 1.
-   At maximum holding period: exit remaining shares at Close.
-   Apply common costs/slippage.

## 7. Mandatory outputs

For A/B/C/D: - Number of signals. - Number of executed trades. - Average
and median net return/trade. - Win rate. - Profit Factor. - Total
portfolio return. - Annualized return where meaningful. - Daily
mark-to-market maximum drawdown. - Return / MDD. - Fold/calendar-period
consistency where applicable. - Concentration: contribution of top 5 and
top 10 winners. - Slippage sensitivity at 0.10%, 0.20%, 0.30%. - For
C/D: all selected signals versus portfolio-executed subset. - For D: Buy
Zone filled / expired / ambiguous / T1 / T2 / stop / time-exit counts.

## 8. Pre-registered PASS / FAIL criteria

The primary question is whether Frozen V2 has economically useful
incremental value over A.

### B PASS

B is considered a PASS only if, at base costs: 1. B's like-for-like net
return exceeds A's net return; AND 2. B has positive net expectancy; AND
3. B Profit Factor \> 1.00; AND 4. B does not achieve superiority solely
through a few extreme winners (top-10 winner concentration must be
explicitly reviewed); AND 5. The advantage is not destroyed by the 0.20%
slippage sensitivity test.

If these conditions are not met, B is NOT PROVEN / FAIL for deployment
evidence.

### C incremental PASS

C passes only if it improves on B on risk-adjusted results without
materially degrading expectancy: - C must have positive net expectancy
and PF \> 1.00; AND - C must improve either net portfolio return with
comparable/lower MDD, or Return/MDD materially versus B; AND -
improvement must not be explained only by portfolio-capacity
differences.

Otherwise Decision Layer is NOT PROVEN to add value.

### D incremental PASS

D passes only if it improves practical risk-adjusted execution versus
C/B: - positive net expectancy and PF \> 1.00; AND - lower MDD or
clearly better Return/MDD than C/B; AND - result remains directionally
acceptable at 0.20% slippage; AND - result is not dependent on excluding
AMBIGUOUS trades.

### Final interpretation

-   PASS means evidence on this holdout supports the frozen strategy
    under the preregistered test.
-   FAIL/NOT PROVEN means do not tune on 2026 to rescue it.
-   A failed 2026 test may be studied diagnostically only after the
    official result is permanently saved.

## 9. Anti-overfitting lock

After the first official 2026 run: - Save all outputs and code hash. -
Do not overwrite the official result. - Any subsequent modification is a
new research version and 2026 is no longer an untouched holdout for that
version.
