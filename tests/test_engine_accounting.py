from iskander_trade.config import Settings
from iskander_trade.engine import PaperTradingEngine


class UnusedMarketData:
    def fetch_spread_pct(self, symbol):
        return 0.1


def test_close_position_records_net_pnl_after_entry_exit_fees_and_slippage():
    settings = Settings(taker_fee_rate=0.001, slippage_rate=0.0005)
    state = {
        "positions": {
            "BTC/USDT": {
                "entry_price": 100.0,
                "quantity": 0.1,
                "entry_fee_usdt": 0.01,
            }
        },
        "daily_pnl_usdt": 0.0,
        "total_realized_pnl_usdt": 0.0,
        "trade_history": [],
    }
    engine = PaperTradingEngine(settings, UnusedMarketData(), state)

    engine._close_position("BTC/USDT", 110.0, "take_profit")

    exit_price = 110.0 * (1.0 - settings.slippage_rate - 0.1 / 200.0)
    expected = (exit_price - 100.0) * 0.1 - 0.01 - exit_price * 0.1 * 0.001
    assert state["trade_history"][-1]["pnl_usdt"] == expected
    assert state["trade_history"][-1]["fees_usdt"] > 0
    assert state["daily_pnl_usdt"] == expected
    assert "BTC/USDT" not in state["positions"]
