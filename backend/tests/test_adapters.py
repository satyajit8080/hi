"""Venue message normalisation — sign conventions and sequence fields."""
from __future__ import annotations

from app.market.adapters.binance import BinanceAdapter
from app.market.adapters.bybit import BybitAdapter
from app.market.adapters.coinbase import CoinbaseAdapter
from app.market.adapters.okx import OKXAdapter


def test_binance_aggtrade_sign_and_depth_fields():
    a = BinanceAdapter(["BTCUSDT"])
    t = a._normalise({"data": {"e": "aggTrade", "E": 1, "s": "BTCUSDT", "T": 5, "p": "64000.1",
                               "q": "0.5", "m": True, "a": 77}})
    assert t.kind == "trade" and t.payload["is_buyer_maker"] is True and t.payload["trade_id"] == 77
    d = a._normalise({"data": {"e": "depthUpdate", "E": 1, "s": "BTCUSDT", "U": 10, "u": 12, "pu": 9,
                               "b": [["64000", "1"]], "a": [["64001", "0"]]}})
    assert d.kind == "book_diff"
    assert (d.payload["first_update_id"], d.payload["final_update_id"], d.payload["prev_update_id"]) == (10, 12, 9)
    assert d.payload["asks"] == [(64001.0, 0.0)]


def test_binance_force_order_maps_sell_to_long_liquidation():
    a = BinanceAdapter(["BTCUSDT"], futures=True)
    ev = a._normalise({"data": {"e": "forceOrder", "E": 1, "o": {"s": "BTCUSDT", "S": "SELL", "ap": "63900",
                                                                  "p": "63890", "q": "2", "T": 9}}})
    assert ev.kind == "liquidation" and ev.payload["side"] == "long" and ev.payload["price"] == 63900.0


def test_binance_stream_url_covers_all_streams():
    url = BinanceAdapter(["BTCUSDT", "ETHUSDT"], futures=True)._stream_url()
    for s in ("btcusdt@depth@100ms", "btcusdt@aggTrade", "btcusdt@bookTicker", "btcusdt@forceOrder", "ethusdt@aggTrade"):
        assert s in url


def test_coinbase_symbol_mapping_and_trade_side():
    c = CoinbaseAdapter(["BTCUSDT"])
    assert c.to_venue_symbol("BTCUSDT") == "BTC-USD" and c.from_venue_symbol("BTC-USD") == "BTCUSDT"
    evs = c._normalise({"channel": "market_trades", "events": [{"trades": [
        {"product_id": "BTC-USD", "price": "64000", "size": "0.2", "side": "SELL", "trade_id": "x1",
         "time": "2026-01-01T00:00:00Z"}]}]})
    assert evs[0].kind == "trade" and evs[0].payload["is_buyer_maker"] is True


def test_okx_snapshot_then_update_sequence():
    o = OKXAdapter(["BTCUSDT"])
    snap = o.normalise({"arg": {"channel": "books", "instId": "BTC-USDT"}, "action": "snapshot",
                        "data": [{"bids": [["64000", "1", "0", "1"]], "asks": [["64001", "1", "0", "1"]],
                                  "seqId": 500, "prevSeqId": -1, "ts": "1"}]})
    assert snap[0].kind == "book_snapshot" and snap[0].payload["last_update_id"] == 500
    upd = o.normalise({"arg": {"channel": "books", "instId": "BTC-USDT"}, "action": "update",
                       "data": [{"bids": [["64000", "2", "0", "1"]], "asks": [], "seqId": 501,
                                 "prevSeqId": 500, "ts": "2"}]})
    assert upd[0].kind == "book_diff"
    assert upd[0].payload["prev_update_id"] == 500 and upd[0].payload["final_update_id"] == 501


def test_okx_trade_side_convention():
    o = OKXAdapter(["BTCUSDT"])
    evs = o.normalise({"arg": {"channel": "trades", "instId": "BTC-USDT"},
                       "data": [{"px": "64000", "sz": "0.3", "side": "sell", "tradeId": "9", "ts": "1"}]})
    assert evs[0].payload["is_buyer_maker"] is True and evs[0].symbol == "BTCUSDT"


def test_bybit_snapshot_delta_and_trade():
    b = BybitAdapter(["BTCUSDT"])
    snap = b.normalise({"topic": "orderbook.50.BTCUSDT", "type": "snapshot", "ts": 1,
                        "data": {"s": "BTCUSDT", "b": [["64000", "1"]], "a": [["64001", "1"]], "u": 10, "seq": 1}})
    assert snap[0].kind == "book_snapshot" and snap[0].payload["last_update_id"] == 10
    delta = b.normalise({"topic": "orderbook.50.BTCUSDT", "type": "delta", "ts": 2,
                         "data": {"s": "BTCUSDT", "b": [["64000", "0"]], "a": [], "u": 11, "seq": 2}})
    assert delta[0].kind == "book_diff" and delta[0].payload["bids"] == [(64000.0, 0.0)]
    tr = b.normalise({"topic": "publicTrade.BTCUSDT", "ts": 3,
                      "data": [{"T": 3, "s": "BTCUSDT", "S": "Sell", "v": "0.1", "p": "64000", "i": "t1"}]})
    assert tr[0].kind == "trade" and tr[0].payload["is_buyer_maker"] is True


def test_bybit_restart_snapshot_u_equals_one():
    b = BybitAdapter(["BTCUSDT"])
    ev = b.normalise({"topic": "orderbook.50.BTCUSDT", "type": "delta", "ts": 1,
                      "data": {"s": "BTCUSDT", "b": [], "a": [], "u": 1, "seq": 1}})
    assert ev[0].kind == "book_snapshot"
