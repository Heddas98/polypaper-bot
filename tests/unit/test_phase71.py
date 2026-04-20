"""
Phase 71: Cross-Platform Intelligence Tests
=============================================
Tests PMXT bridge, Whale tracker, EventWaves, Latency, Spread signal.
"""
import pytest
import time
import os

# Isolate from global .env restrictions
os.environ["EVENT_WAVES_ENABLED"] = "true"
os.environ["SPREAD_SIGNAL_ENABLED"] = "true"


# ═══ PMXT Bridge Tests ═══

class TestPMXTBridge:
    def test_cross_platform_edge_no_data(self):
        from data_feeds.pmxt_bridge import PMXTBridge
        bridge = PMXTBridge()
        edge = bridge.get_edge("BTC", 0.55)
        assert edge.n_platforms == 0
        assert edge.signal == 0.0

    def test_cross_platform_edge_with_cache(self):
        from data_feeds.pmxt_bridge import PMXTBridge, CrossPlatformPrice
        bridge = PMXTBridge()
        bridge._cache["BTC"] = [
            CrossPlatformPrice(
                platform="kalshi", yes_price=0.60, no_price=0.40,
                timestamp=time.time())
        ]
        edge = bridge.get_edge("BTC", 0.55)
        assert edge.n_platforms == 1
        assert edge.avg_cross_price == 0.60
        assert edge.divergence == -0.05  # poly < kalshi

    def test_cross_platform_signal_direction(self):
        from data_feeds.pmxt_bridge import PMXTBridge, CrossPlatformPrice
        bridge = PMXTBridge()
        bridge._cache["ETH"] = [
            CrossPlatformPrice(
                platform="kalshi", yes_price=0.70, no_price=0.30,
                timestamp=time.time())
        ]
        # Poly at 0.55 vs Kalshi at 0.70 → poly underpriced → positive signal
        edge = bridge.get_edge("ETH", 0.55)
        assert edge.signal > 0  # Buy signal (poly underpriced)

    def test_cross_platform_stale_data(self):
        from data_feeds.pmxt_bridge import PMXTBridge, CrossPlatformPrice
        bridge = PMXTBridge()
        bridge._cache["BTC"] = [
            CrossPlatformPrice(
                platform="kalshi", yes_price=0.60,
                timestamp=time.time() - 2000)  # Very old
        ]
        edge = bridge.get_edge("BTC", 0.55)
        assert edge.stale is True

    def test_cross_platform_small_divergence(self):
        from data_feeds.pmxt_bridge import PMXTBridge, CrossPlatformPrice
        bridge = PMXTBridge()
        bridge._cache["BTC"] = [
            CrossPlatformPrice(
                platform="kalshi", yes_price=0.56, no_price=0.44,
                timestamp=time.time())
        ]
        # Divergence = 0.55 - 0.56 = -0.01, below min threshold (0.03)
        edge = bridge.get_edge("BTC", 0.55)
        assert edge.signal == 0.0

    def test_format_telegram(self):
        from data_feeds.pmxt_bridge import PMXTBridge
        bridge = PMXTBridge()
        text = bridge.format_telegram()
        assert "Veri yok" in text

    def test_kalshi_map(self):
        from data_feeds.pmxt_bridge import PMXTBridge
        assert PMXTBridge.KALSHI_MAP["BTC"] == "KXBTC"
        assert PMXTBridge.KALSHI_MAP["ETH"] == "KXETH"


# ═══ Whale Tracker Tests ═══

class TestWhaleTracker:
    def test_empty_signal(self):
        from data_feeds.whale_tracker import WhaleTracker
        tracker = WhaleTracker()
        signal = tracker.compute_signal([])
        assert signal.n_whale_trades == 0
        assert signal.signal == 0.0

    def test_whale_flow_direction(self):
        from data_feeds.whale_tracker import WhaleTracker, WhaleTrade
        tracker = WhaleTracker()
        trades = [
            WhaleTrade(direction="up", amount_usd=2000, timestamp=time.time()),
            WhaleTrade(direction="up", amount_usd=1000, timestamp=time.time()),
            WhaleTrade(direction="down", amount_usd=500, timestamp=time.time()),
        ]
        signal = tracker.compute_signal(trades)
        assert signal.n_whale_trades == 3
        assert signal.net_flow > 0  # Net buying YES
        assert signal.signal > 0

    def test_whale_orderbook_depth(self):
        from data_feeds.whale_tracker import WhaleTracker
        tracker = WhaleTracker()
        orderbook = {
            "bids": [{"size": 10000, "price": 0.55}],
            "asks": [{"size": 100, "price": 0.56}],
        }
        has_anomaly, side, usd = tracker.analyze_orderbook_depth(orderbook)
        assert has_anomaly is True
        assert side == "bid"
        assert usd > 5000

    def test_whale_no_anomaly(self):
        from data_feeds.whale_tracker import WhaleTracker
        tracker = WhaleTracker()
        orderbook = {
            "bids": [{"size": 10, "price": 0.55}],
            "asks": [{"size": 10, "price": 0.56}],
        }
        has_anomaly, _, _ = tracker.analyze_orderbook_depth(orderbook)
        assert has_anomaly is False

    def test_whale_format(self):
        from data_feeds.whale_tracker import WhaleTracker, WhaleFlowSignal
        tracker = WhaleTracker()
        signal = WhaleFlowSignal()
        text = tracker.format_telegram(signal)
        assert "Aktivite yok" in text


# ═══ EventWaves Market Quality Tests ═══

class TestEventWaves:
    def test_good_market(self):
        from data_feeds.event_waves import assess_market_quality
        quality = assess_market_quality(
            slug="btc-up-or-down",
            volume_24h=50000,
            spread=0.02,
            n_traders=50,
            up_odds=0.55)
        assert quality.should_trade is True
        assert quality.score > 0.5

    def test_low_volume(self):
        from data_feeds.event_waves import assess_market_quality
        quality = assess_market_quality(
            slug="test",
            volume_24h=100,
            spread=0.02,
            up_odds=0.55)
        assert quality.should_trade is False
        assert "low_vol" in quality.reason

    def test_wide_spread(self):
        from data_feeds.event_waves import assess_market_quality
        quality = assess_market_quality(
            slug="test",
            volume_24h=5000,
            spread=0.15,
            up_odds=0.55)
        assert quality.should_trade is False
        assert "wide_spread" in quality.reason

    def test_quality_scoring(self):
        from data_feeds.event_waves import assess_market_quality
        # Compare good vs bad market
        good = assess_market_quality(
            slug="good", volume_24h=100000, spread=0.01,
            n_traders=100, up_odds=0.55)
        bad = assess_market_quality(
            slug="bad", volume_24h=500, spread=0.08,
            n_traders=5, up_odds=0.50)
        assert good.score > bad.score

    def test_format_quality(self):
        from data_feeds.event_waves import format_quality_telegram, MarketQuality
        q = MarketQuality(score=0.75, slug="test", should_trade=True, reason="ok")
        text = format_quality_telegram(q)
        assert "🟢" in text
        assert "test" in text


# ═══ Latency Monitor Tests ═══

class TestLatencyMonitor:
    def test_empty_stats(self):
        from data_feeds.latency_monitor import LatencyMonitor
        monitor = LatencyMonitor()
        stats = monitor.get_stats()
        assert stats.n_samples == 0
        assert "insufficient" in stats.reason

    def test_record_events(self):
        from data_feeds.latency_monitor import LatencyMonitor
        monitor = LatencyMonitor()
        now = time.time() * 1000
        # Record Binance event
        monitor.record_binance_change("BTC", 50000, now)
        assert len(monitor._binance_events) == 1
        # Record Poly event 2s later
        monitor.record_poly_change("BTC", 0.55, now + 2000)
        assert len(monitor._poly_events) == 1

    def test_lag_matching(self):
        from data_feeds.latency_monitor import LatencyMonitor
        monitor = LatencyMonitor()
        now = time.time() * 1000
        # Simulate 30 events with 1.5s lag
        for i in range(30):
            ts = now + i * 10000
            monitor.record_binance_change("BTC", 50000 + i, ts)
            monitor.record_poly_change("BTC", 0.55 + i * 0.001, ts + 1500)

        stats = monitor.get_stats()
        assert stats.n_samples >= 20
        assert 1000 < stats.mean_lag_ms < 2000  # ~1500ms

    def test_lag_arb_viable(self):
        from data_feeds.latency_monitor import LatencyMonitor
        monitor = LatencyMonitor()
        now = time.time() * 1000
        for i in range(30):
            ts = now + i * 10000
            monitor.record_binance_change("BTC", 50000, ts)
            monitor.record_poly_change("BTC", 0.55, ts + 2000)  # 2s lag

        stats = monitor.get_stats()
        assert stats.lag_arb_viable is True
        assert stats.max_capital_pct > 0

    def test_format_telegram(self):
        from data_feeds.latency_monitor import LatencyMonitor
        monitor = LatencyMonitor()
        text = monitor.format_telegram()
        assert "Yeterli veri yok" in text


# ═══ Spread Signal Tests ═══

class TestSpreadSignal:
    def test_no_orderbook(self):
        from data_feeds.spread_signal import analyze_spread
        result = analyze_spread(None)
        assert result.reason == "no_orderbook"

    def test_basic_analysis(self):
        from data_feeds.spread_signal import analyze_spread
        ob = {
            "bids": [
                {"price": 0.54, "size": 1000},
                {"price": 0.53, "size": 500},
            ],
            "asks": [
                {"price": 0.56, "size": 800},
                {"price": 0.57, "size": 300},
            ],
        }
        result = analyze_spread(ob, current_price=0.55, minutes_remaining=5.0)
        assert result.spread == 0.02  # 0.56 - 0.54
        assert result.bid_depth_usd > 0
        assert result.ask_depth_usd > 0

    def test_imbalance_bullish(self):
        from data_feeds.spread_signal import analyze_spread
        ob = {
            "bids": [{"price": 0.54, "size": 5000}],
            "asks": [{"price": 0.56, "size": 100}],
        }
        result = analyze_spread(ob)
        assert result.imbalance > 0  # More bids = bullish

    def test_imbalance_bearish(self):
        from data_feeds.spread_signal import analyze_spread
        ob = {
            "bids": [{"price": 0.54, "size": 100}],
            "asks": [{"price": 0.56, "size": 5000}],
        }
        result = analyze_spread(ob)
        assert result.imbalance < 0  # More asks = bearish

    def test_fill_probability_near_close(self):
        from data_feeds.spread_signal import analyze_spread
        ob = {
            "bids": [{"price": 0.54, "size": 1000}],
            "asks": [{"price": 0.55, "size": 1000}],
        }
        near = analyze_spread(ob, minutes_remaining=1.0)
        far = analyze_spread(ob, minutes_remaining=30.0)
        assert near.fill_probability >= far.fill_probability

    def test_format_spread(self):
        from data_feeds.spread_signal import format_spread_telegram, SpreadAnalysis
        sa = SpreadAnalysis(spread=0.02, imbalance=0.3)
        text = format_spread_telegram(sa)
        assert "Spread Analysis" in text
