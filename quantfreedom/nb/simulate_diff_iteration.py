import numpy as np
from numba import njit
from datetime import datetime

from quantfreedom.nb.candle_iteration import *

from quantfreedom._typing import PossibleArray, Array1d, RecordArray
from quantfreedom.enums.enums import (
    AccountState,
    OrderResult,
    OrderSettings,
    PriceArrayTuple,
    PriceFloatTuple,
    StaticVariables,
    OrderSettingsArrays,
    or_dt,
    order_settings_array_dt,
    strat_df_array_dt,
    strat_records_dt,
)
from quantfreedom.nb.execute_funcs import (
    check_sl_tp_nb,
    process_order_nb,
)
from quantfreedom.nb.helper_funcs import (
    fill_order_settings_result_records_nb,
    fill_strategy_result_records_nb,
    get_to_the_upside_nb,
)


@njit(cache=True)
def get_order_settings(
    settings_idx: int, os_cart_arrays_tuple: OrderSettingsArrays
) -> OrderSettings:
    return OrderSettings(
        leverage=os_cart_arrays_tuple.leverage[settings_idx],
        max_equity_risk_pct=os_cart_arrays_tuple.max_equity_risk_pct[settings_idx],
        max_equity_risk_value=os_cart_arrays_tuple.max_equity_risk_value[settings_idx],
        risk_reward=os_cart_arrays_tuple.risk_reward[settings_idx],
        size_pct=os_cart_arrays_tuple.size_pct[settings_idx],
        size_value=os_cart_arrays_tuple.size_value[settings_idx],
        sl_based_on=os_cart_arrays_tuple.sl_based_on[settings_idx],
        sl_based_on_add_pct=os_cart_arrays_tuple.sl_based_on_add_pct[settings_idx],
        sl_based_on_lookback=os_cart_arrays_tuple.sl_based_on_lookback[settings_idx],
        sl_pct=os_cart_arrays_tuple.sl_pct[settings_idx],
        sl_to_be_based_on=os_cart_arrays_tuple.sl_to_be_based_on[settings_idx],
        sl_to_be_zero_or_entry=os_cart_arrays_tuple.sl_to_be_zero_or_entry[
            settings_idx
        ],
        sl_to_be_when_pct_from_avg_entry=os_cart_arrays_tuple.sl_to_be_when_pct_from_avg_entry[
            settings_idx
        ],
        tp_pct=os_cart_arrays_tuple.tp_pct[settings_idx],
        trail_sl_based_on=os_cart_arrays_tuple.trail_sl_based_on[settings_idx],
        trail_sl_by_pct=os_cart_arrays_tuple.trail_sl_by_pct[settings_idx],
        trail_sl_when_pct_from_avg_entry=os_cart_arrays_tuple.trail_sl_when_pct_from_avg_entry[
            settings_idx
        ],
    )


@njit(cache=True)
def get_interest_prices(
    bar_idx: int, prices, settings: OrderSettings
) -> PriceArrayTuple:
    open_prices, high_prices, low_prices, close_prices = prices
    if not np.isnan(settings.sl_based_on):
        lb = max(int(bar_idx - settings.sl_based_on_lookback), 0)
        prices = PriceArrayTuple(
            entry=open_prices[bar_idx],
            open=open_prices[lb : bar_idx + 1],
            high=high_prices[lb : bar_idx + 1],
            low=low_prices[lb : bar_idx + 1],
            close=close_prices[lb : bar_idx + 1],
        )
    else:
        prices = PriceArrayTuple(
            entry=open_prices[bar_idx],
            open=open_prices[0:1],
            high=open_prices[0:1],
            low=open_prices[0:1],
            close=open_prices[0:1],
        )
    return prices


@njit(cache=True)
def backtest_df_only_nb(
    num_of_symbols: int,
    total_indicator_settings: int,
    total_order_settings: int,
    total_bars: int,
    # entry info
    entries: PossibleArray,
    price_data: PossibleArray,
    # Tuples
    static_variables_tuple: StaticVariables,
    os_cart_arrays_tuple: OrderSettingsArrays,
) -> Array1d[Array1d, Array1d]:
    # Creating strat records
    array_size = int(
        num_of_symbols
        * total_indicator_settings
        * total_order_settings
        / static_variables_tuple.divide_records_array_size_by
    )

    strategy_result_records = np.empty(
        array_size,
        dtype=strat_df_array_dt,
    )
    order_settings_result_records = np.empty(
        array_size,
        dtype=order_settings_array_dt,
    )
    result_records_filled = 0

    strat_records = np.empty(int(total_bars / 3), dtype=strat_records_dt)
    strat_records_filled = np.array([0])

    prices_start = 0
    entries_per_symbol = int(entries.shape[1] / num_of_symbols)
    entries_start = 0
    entries_end = entries_per_symbol
    entries_col = 0
    prices = 0

    for symbol_counter in range(num_of_symbols):
        open_prices = price_data[:, prices_start]
        high_prices = price_data[:, prices_start + 1]
        low_prices = price_data[:, prices_start + 2]
        close_prices = price_data[:, prices_start + 3]

        # create Df from two-dimensional ndarray
        candles = pd.DataFrame(
            price_data[:, prices_start : prices_start + 4],
            columns=["open", "high", "low", "close"],
        )
        candles["timestamp"] = np.repeat(datetime.now(), candles.shape[0])

        prices_start += 4

        symbol_entries = entries[:, entries_start:entries_end]
        entries_start = entries_end
        entries_end += entries_per_symbol

        # ind set loop
        for indicator_settings_counter in range(entries_per_symbol):
            current_indicator_entries = symbol_entries[:, indicator_settings_counter]
            print(
                f"[NUMBER_TRUE_ENTRIES={np.count_nonzero(current_indicator_entries)}]"
            )

            for order_settings_idx in range(total_order_settings):
                order_settings = get_order_settings(
                    order_settings_idx, os_cart_arrays_tuple
                )
                # Account State Reset
                account_state = AccountState(
                    available_balance=static_variables_tuple.equity,
                    cash_borrowed=0.0,
                    cash_used=0.0,
                    equity=static_variables_tuple.equity,
                )

                # Order Result Reset
                order_result = OrderResult(
                    average_entry=0.0,
                    fees_paid=0.0,
                    leverage=0.0,
                    liq_price=np.nan,
                    moved_sl_to_be=False,
                    order_status=0,
                    order_status_info=0,
                    order_type=static_variables_tuple.order_type,
                    pct_chg_trade=0.0,
                    position=0.0,
                    price=0.0,
                    realized_pnl=0.0,
                    size_value=0.0,
                    sl_pct=0.0,
                    sl_price=0.0,
                    tp_pct=0.0,
                    tp_price=0.0,
                )
                strat_records_filled[0] = 0

                logging.info(
                    f"[STARTING_ITERATION] [SYMBOL={symbol_counter}] INDICATOR=[{indicator_settings_counter}] [ORDER_SETTING={order_settings_idx}]"
                )
                logging.info(
                    f"[NUMBER_CANDLES={candles.shape[0]}] [NUMBER_TRUE_SIGNALS={np.count_nonzero(current_indicator_entries)}]"
                )

                # entries loop
                iteration = CandleIteration(
                    user_configuration={},
                    candles=candles,
                    entry_signals=current_indicator_entries,
                    evaluate_entry_signal=evaluate_entry_signal,
                    evaluate_increase_position=evaluate_increase_position,
                    evaluate_stop_loss=evaluate_stop_loss,
                    evaluate_take_profit=evaluate_take_profit,
                    place_entry=place_entry,
                    close_position=close_position,
                    adjust_stop_loss_trailing=adjust_stop_loss_trailing,
                )

                iteration.iterate()

    return (
        strategy_result_records[:result_records_filled],
        order_settings_result_records[:result_records_filled],
    )


@njit(cache=True)
def _sim_6(
    entries,
    price_data,
    static_variables_tuple: StaticVariables,
    os_broadcast_arrays: OrderSettingsArrays,
) -> tuple[Array1d, RecordArray]:
    total_bars = entries.shape[0]
    order_records = np.empty(total_bars * 2, dtype=or_dt)
    order_records_id = np.array([0])

    prices_start = 0
    for settings_counter in range(entries.shape[1]):
        open_prices = price_data[:, prices_start]
        high_prices = price_data[:, prices_start + 1]
        low_prices = price_data[:, prices_start + 2]
        close_prices = price_data[:, prices_start + 3]
        prices_start += 4

        curr_entries = entries[:, settings_counter]
        order_settings = OrderSettings(
            leverage=os_broadcast_arrays.leverage[settings_counter],
            max_equity_risk_pct=os_broadcast_arrays.max_equity_risk_pct[
                settings_counter
            ],
            max_equity_risk_value=os_broadcast_arrays.max_equity_risk_value[
                settings_counter
            ],
            risk_reward=os_broadcast_arrays.risk_reward[settings_counter],
            size_pct=os_broadcast_arrays.size_pct[settings_counter],
            size_value=os_broadcast_arrays.size_value[settings_counter],
            sl_based_on=os_broadcast_arrays.sl_based_on[settings_counter],
            sl_based_on_add_pct=os_broadcast_arrays.sl_based_on_add_pct[
                settings_counter
            ],
            sl_based_on_lookback=os_broadcast_arrays.sl_based_on_lookback[
                settings_counter
            ],
            sl_pct=os_broadcast_arrays.sl_pct[settings_counter],
            sl_to_be_based_on=os_broadcast_arrays.sl_to_be_based_on[settings_counter],
            sl_to_be_zero_or_entry=os_broadcast_arrays.sl_to_be_zero_or_entry[
                settings_counter
            ],
            sl_to_be_when_pct_from_avg_entry=os_broadcast_arrays.sl_to_be_when_pct_from_avg_entry[
                settings_counter
            ],
            tp_pct=os_broadcast_arrays.tp_pct[settings_counter],
            trail_sl_based_on=os_broadcast_arrays.trail_sl_based_on[settings_counter],
            trail_sl_by_pct=os_broadcast_arrays.trail_sl_by_pct[settings_counter],
            trail_sl_when_pct_from_avg_entry=os_broadcast_arrays.trail_sl_when_pct_from_avg_entry[
                settings_counter
            ],
        )
        # Account State Reset
        account_state = AccountState(
            available_balance=static_variables_tuple.equity,
            cash_borrowed=0.0,
            cash_used=0.0,
            equity=static_variables_tuple.equity,
        )

        # Order Result Reset
        order_result = OrderResult(
            average_entry=0.0,
            fees_paid=0.0,
            leverage=0.0,
            liq_price=np.nan,
            moved_sl_to_be=False,
            order_status=0,
            order_status_info=0,
            order_type=static_variables_tuple.order_type,
            pct_chg_trade=0.0,
            position=0.0,
            price=0.0,
            realized_pnl=0.0,
            size_value=0.0,
            sl_pct=0.0,
            sl_price=0.0,
            tp_pct=0.0,
            tp_price=0.0,
        )

        # entries loop
        for bar in range(total_bars):
            if account_state.available_balance < 5:
                break

            if curr_entries[bar]:
                if not np.isnan(order_settings.sl_based_on):
                    lb = int(bar - order_settings.sl_based_on_lookback)
                    if lb < 0:
                        prices = PriceArrayTuple(
                            entry=open_prices[bar],
                            open=open_prices[0 : bar + 1],
                            high=high_prices[0 : bar + 1],
                            low=low_prices[0 : bar + 1],
                            close=close_prices[0 : bar + 1],
                        )
                    else:
                        prices = PriceArrayTuple(
                            entry=open_prices[bar],
                            open=open_prices[lb : bar + 1],
                            high=high_prices[lb : bar + 1],
                            low=low_prices[lb : bar + 1],
                            close=close_prices[lb : bar + 1],
                        )

                else:
                    prices = PriceArrayTuple(
                        entry=open_prices[bar],
                        open=open_prices[0:2],
                        high=high_prices[0:2],
                        low=low_prices[0:2],
                        close=close_prices[0:2],
                    )
                # Process Order nb
                account_state, order_result = process_order_nb(
                    account_state=account_state,
                    bar=bar,
                    order_result=order_result,
                    order_settings_counter=settings_counter,
                    order_settings=order_settings,
                    order_type=static_variables_tuple.order_type,
                    prices=prices,
                    static_variables_tuple=static_variables_tuple,
                    order_records_id=order_records_id,
                    order_records=order_records[order_records_id[0]],
                )
                if order_result.position > 0:
                    prices_check_stops = PriceFloatTuple(
                        entry=open_prices[bar],
                        open=open_prices[bar],
                        high=high_prices[bar],
                        low=low_prices[bar],
                        close=close_prices[bar],
                    )
                    # Check Stops
                    order_result = check_sl_tp_nb(
                        account_state=account_state,
                        bar=bar,
                        order_result=order_result,
                        order_settings_counter=settings_counter,
                        order_settings_tuple=order_settings,
                        prices_tuple=prices_check_stops,
                        static_variables_tuple=static_variables_tuple,
                    )
                    # process stops
                    if not np.isnan(order_result.size_value):
                        account_state, order_result = process_order_nb(
                            account_state=account_state,
                            bar=bar,
                            order_result=order_result,
                            order_settings_counter=settings_counter,
                            order_settings=order_settings,
                            order_type=order_result.order_type,
                            prices=prices,
                            static_variables_tuple=static_variables_tuple,
                            order_records_id=order_records_id,
                            order_records=order_records[order_records_id[0]],
                        )

    return order_records[: order_records_id[-1]]
