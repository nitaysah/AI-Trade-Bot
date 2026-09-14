# Quantitative research protocol

Apply relevant sections when researching strategies, changing backtest or signal logic, optimizing parameters, or reporting performance. Routine UI work does not require a research exercise. This protocol complements the root `AGENTS.md`.

## Experiment design and reproducibility

- State the economic or market-structure hypothesis, universe, instrument, direction, timeframe, session, holding horizon, benchmark, and rejection criteria.
- Predefine parameter ranges, selection criteria, risk constraints, and evaluation periods. Record changes instead of silently revising criteria after seeing results.
- Preserve a run manifest: code version and relevant uncommitted diff, effective settings, provider/feed, data snapshot or checksum, date range, timezone/calendar, dependencies, cost model, and random seeds where applicable.
- Label exploration as exploratory. Requested profit or trade-count targets must not determine which failed candidates are hidden.

## Data and decision timing

- Use historical point-in-time inputs and eligible universes where available. Disclose missing delisted assets, revised data, unavailable history, and other potential bias.
- Distinguish event/publication time, bar open/close, decision, submission, and execution. Simulate fills no earlier than information availability and modeled latency permit. Completed-bar strategies normally execute on a subsequent eligible quote/bar.
- Fit preprocessing, normalization, feature selection, and models only on each training period, never on the entire dataset before validation.
- Check gaps, duplicates, ordering, warmup, adjustments, timezones, and provider coverage. Disclose material feed differences rather than assuming providers must match exactly.
- Keep adjusted research prices, executable prices, position quantities, and corporate-action handling consistent.
- Historical AI/news experiments require publication-time inputs. Modern models may know future events despite historical prompts; disclose this leakage risk and do not claim a clean historical test without evidence.

## Overfitting and validation

- Separate chronological training, validation, and an untouched final test period. Use walk-forward evaluation where appropriate; never repeatedly optimize against the final holdout.
- For overlapping labels or holding periods, purge overlapping observations and use a justified time gap/embargo where needed. Randomly shuffled validation is not the default for dependent financial observations.
- Record all parameter trials and the selection process, including failures. For substantial searches, assess multiple-testing/selection effects using appropriate methods and disclose assumptions; do not claim statistical significance from the winning Sharpe ratio alone.
- Check neighboring parameters, multiple regimes, and relevant instruments. Prefer stable behavior over a narrow isolated optimum.
- Report small samples honestly. Trade count alone does not establish independent observations or statistical reliability.

## Executable outcomes

- Include applicable fees, spread, slippage, latency, liquidity/participation limits, funding/borrow costs, and cash/margin constraints. State what is modeled and what is unavailable.
- Do not assume touched limit orders fill, unlimited liquidity, unsupported fractional quantities, or stop-price execution through gaps. Model partial fills and order lifecycle behavior when material.
- If one candle touches both stop and target, use sufficient intrabar data or an explicitly conservative ordering assumption. Never silently choose the favorable outcome.
- Options require historical contract identity, strike, expiry, multiplier, quote/trade availability, spread, and applicable exercise/assignment and settlement handling. Disclose expiration and illiquidity assumptions. Label underlying-only or synthetic premium simulations as approximations.
- Reconcile realized/unrealized P&L, costs, equity, and remaining positions. Model shared capital and simultaneous exposures; independently profitable bot backtests cannot simply be summed.

## Reporting and promotion

- Report net return, a comparable benchmark, maximum drawdown and duration, exposure, turnover, trade count, win rate, average win/loss, and costs. Include appropriate risk-adjusted metrics with sampling and annualization assumptions.
- Align benchmark dates, capital, instruments, and dividend/cost treatment. Distinguish percentage-point outperformance from relative percentage improvement.
- Separate in-sample, validation, final test, and paper forward results. Present requested candidates under consistent selection criteria.
- Estimate uncertainty where defensible, accounting for serial dependence. State when available data cannot support a reliable estimate.
- Stress-test reasonable changes in costs, latency, missed fills, gaps, and parameters. Report reliance on a few trades, one instrument, or one regime.
- Report failures and unavailable data. Never fabricate runs, results, downloaded datasets, or verification. No tested edge is a valid conclusion.
- Freeze candidate settings before paper forward testing. Define acceptance criteria and monitoring for data drift, signal frequency, slippage, and divergence from expected behavior.
- Before proposing live activation, provide reproducible evidence, risk settings, recovery behavior, and rollback instructions. Research results never authorize changes to live bots, orders, risk limits, or production records.

## References

- [Bailey et al., The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf): selection effects from repeated backtest searches.
- [FINRA Regulatory Notice 15-09](https://www.finra.org/rules-guidance/notices/15-09): algorithm-development, testing, and control practices. Used as an engineering reference, not an assertion that member-firm obligations apply to this project.
