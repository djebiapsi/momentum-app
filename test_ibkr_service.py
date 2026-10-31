# -*- coding: utf-8 -*-
"""Tests du plan de rééquilibrage IBKR (logique pure, sans gateway)."""
import pytest

from ibkr_service import plan_rebalance, _floor_qty, MIN_ORDER_VALUE_USD


def pos(ticker, qty, price, con_id=1):
    return {'ticker': ticker, 'qty': qty, 'market_price': price, 'market_value': qty * price,
            'currency': 'USD', 'con_id': con_id, 'primary_exchange': 'NASDAQ'}


class TestFloorQty:
    def test_fractional_floors_to_4_decimals(self):
        assert _floor_qty(1.18343, True) == 1.1834
        assert _floor_qty(3.00003, True) == 3.0

    def test_integer_mode_floors(self):
        assert _floor_qty(1.99, False) == 1.0
        assert _floor_qty(0.6, False) == 0.0


class TestPlanRebalance:
    def test_order_sequence_liquidations_sells_then_buys(self):
        positions = [pos('OLD', 2, 100), pos('BIG', 10, 100)]        # OLD hors cibles, BIG surpondéré
        plan = plan_rebalance(positions, 2000, [{'ticker': 'BIG', 'target_pct': 25},
                                                {'ticker': 'NEW', 'target_pct': 25}],
                              prices={'NEW': 50})
        seq = [(o['ticker'], o['action'], o['liquidation']) for o in plan['orders']]
        assert seq == [('OLD', 'SELL', True), ('BIG', 'SELL', False), ('NEW', 'BUY', False)]

    def test_liquidation_integer_mode_sells_whole_part_and_reports_residual(self):
        # TWS API : 10243 sur toute quantité fractionnaire → on vend 3, il reste 0.3539
        plan = plan_rebalance([pos('WDC', 3.3539, 368.12)], 5000, [{'ticker': 'MU', 'target_pct': 10}],
                              prices={'MU': 889})
        liq = plan['orders'][0]
        assert liq['ticker'] == 'WDC' and liq['action'] == 'SELL' and liq['qty'] == 3.0
        assert liq['residual_qty'] == 0.3539 and 'IBKR Mobile' in liq['note']
        assert liq['est_value'] == round(3 * 368.12, 2)

    def test_liquidation_sub_share_fraction_is_skipped_in_integer_mode(self):
        plan = plan_rebalance([pos('STX', 0.0012, 648.22), pos('SNDK', 0.3929, 1157.5)], 5000,
                              [{'ticker': 'MU', 'target_pct': 20}], prices={'MU': 889})
        assert [(o['ticker'], o['qty']) for o in plan['orders']] == [('MU', 1.0)]   # 1000 $ / 889
        assert sorted(s['ticker'] for s in plan['skipped']) == ['SNDK', 'STX']
        assert all('10243' in s['reason'] for s in plan['skipped'])

    def test_liquidation_exact_qty_in_fractional_mode(self):
        plan = plan_rebalance([pos('STX', 0.0012, 648.22)], 5000, [{'ticker': 'MU', 'target_pct': 10}],
                              prices={'MU': 889}, fractional=True)
        liq = plan['orders'][0]
        assert liq['qty'] == 0.0012 and 'residual_qty' not in liq

    def test_buy_quantity_is_fractional_floor(self):
        plan = plan_rebalance([], 10000, [{'ticker': 'LITE', 'target_pct': 10}], prices={'LITE': 845},
                              fractional=True)
        o = plan['orders'][0]
        assert o['action'] == 'BUY'
        assert o['qty'] == 1.1834           # 1000 / 845 = 1.18343…
        assert o['est_value'] == round(1.1834 * 845, 2)

    def test_integer_mode_is_default_and_skips_sub_share_amounts(self):
        plan = plan_rebalance([], 1000, [{'ticker': 'MU', 'target_pct': 50}], prices={'MU': 889})
        assert plan['orders'] == []
        assert plan['skipped'][0]['ticker'] == 'MU'
        assert 'arrondi' in plan['skipped'][0]['reason']

    def test_small_drift_is_ignored(self):
        plan = plan_rebalance([pos('MU', 1, 1000)], 4000, [{'ticker': 'MU', 'target_pct': 25.3}])
        assert plan['orders'] == []
        assert 'seuil' in plan['skipped'][0]['reason']
        assert abs(1012 - 1000) < MIN_ORDER_VALUE_USD

    def test_sell_is_capped_to_holdings(self):
        plan = plan_rebalance([pos('MU', 1.5, 800)], 1200, [{'ticker': 'MU', 'target_pct': 1}])
        o = plan['orders'][0]
        assert o['action'] == 'SELL' and o['qty'] <= 1.5

    def test_new_ticker_without_price_is_skipped(self):
        plan = plan_rebalance([], 5000, [{'ticker': 'ZZZ', 'target_pct': 20}])
        assert plan['orders'] == []
        assert 'prix' in plan['skipped'][0]['reason']

    def test_held_position_price_wins_over_hint(self):
        plan = plan_rebalance([pos('MU', 1, 800)], 5000, [{'ticker': 'MU', 'target_pct': 32}],
                              prices={'MU': 400})
        o = plan['orders'][0]
        assert o['est_price'] == 800
        assert o['qty'] == 1.0            # (1600 - 800) / 800

    def test_empty_targets_liquidates_everything(self):
        plan = plan_rebalance([pos('A', 1, 10), pos('B', 2.5, 20)], 100, [])
        assert [(o['ticker'], o['qty'], o['liquidation']) for o in plan['orders']] == \
            [('A', 1.0, True), ('B', 2.0, True)]
        assert plan['orders'][1]['residual_qty'] == 0.5
        assert plan['total_target_pct'] == 0

    def test_base_value_is_account_value_not_positions_sum(self):
        # 4000 $ de positions + 1000 $ de cash : 100 % sur MU doit investir le cash
        plan = plan_rebalance([pos('MU', 4, 1000)], 5000, [{'ticker': 'MU', 'target_pct': 100}])
        o = plan['orders'][0]
        assert o['action'] == 'BUY' and o['qty'] == 1.0 and o['target_value'] == 5000

    def test_duplicate_targets_are_merged(self):
        plan = plan_rebalance([], 1000, [{'ticker': 'mu', 'target_pct': 10}, {'ticker': 'MU', 'target_pct': 10}],
                              prices={'MU': 100})
        assert len(plan['orders']) == 1
        assert plan['orders'][0]['qty'] == 2.0
        assert plan['total_target_pct'] == 20

    def test_short_position_is_covered_with_buy(self):
        plan = plan_rebalance([pos('X', -2, 50)], 1000, [{'ticker': 'MU', 'target_pct': 10}], prices={'MU': 100})
        cover = plan['orders'][0]
        assert cover['ticker'] == 'X' and cover['action'] == 'BUY' and cover['qty'] == 2.0

    def test_manual_complements_cover_fractions_and_sub_share_targets(self):
        # Compte réel du 31/10 : WDC hors cibles (3.3539), SNDK et MU < 1 action face à leur cible
        positions = [pos('WDC', 3.3539, 368.12), pos('SNDK', 0.3929, 1157.5), pos('MU', 0.9968, 889.15)]
        plan = plan_rebalance(positions, 4750, [{'ticker': 'SNDK', 'target_pct': 13.32},
                                                {'ticker': 'MU', 'target_pct': 20.55}])
        assert [(o['ticker'], o['qty']) for o in plan['orders']] == [('WDC', 3.0)]   # seul ordre API possible
        manual = {m['ticker']: m for m in plan['manual_orders']}
        assert manual['WDC']['action'] == 'SELL' and manual['WDC']['qty'] == 0.3539
        assert manual['SNDK']['action'] == 'BUY' and manual['SNDK']['cash'] == round(4750 * 0.1332 - 0.3929 * 1157.5, 2)
        assert manual['MU']['action'] == 'BUY' and manual['MU']['cash'] == round(4750 * 0.2055 - 0.9968 * 889.15, 2)
        assert manual['SNDK']['qty'] == round(manual['SNDK']['cash'] / 1157.5, 4)

    def test_manual_complement_after_integer_buy_and_below_threshold(self):
        # 1000 $ visés à 300 $/action → API achète 3 (900 $), reste 100 $ → complément manuel
        plan = plan_rebalance([], 10000, [{'ticker': 'A', 'target_pct': 10}], prices={'A': 300})
        assert plan['orders'][0]['qty'] == 3.0
        assert plan['manual_orders'] == [{'ticker': 'A', 'action': 'BUY', 'cash': 100.0, 'qty': 0.3333,
                                          'est_value': 100.0, 'reason': 'complément fractionnaire vers la cible'}]
        # reste 10 $ → sous le seuil, rien à faire à la main
        plan = plan_rebalance([], 10000, [{'ticker': 'A', 'target_pct': 10}], prices={'A': 330})
        assert plan['orders'][0]['qty'] == 3.0 and plan['manual_orders'] == []

    def test_manual_fractional_trim_is_capped_to_remaining_position(self):
        # 2.6 détenues (780 $), cible 300 $ → API vend 1 (floor 1.6), reste 1.6 → cible 1.0 → manuel SELL 0.6
        plan = plan_rebalance([pos('A', 2.6, 300)], 1000, [{'ticker': 'A', 'target_pct': 30}])
        assert plan['orders'][0]['action'] == 'SELL' and plan['orders'][0]['qty'] == 1.0
        assert plan['manual_orders'] == [{'ticker': 'A', 'action': 'SELL', 'qty': 0.6, 'cash': None,
                                          'est_value': 180.0, 'reason': 'allègement fractionnaire vers la cible'}]

    def test_no_manual_orders_in_fractional_mode(self):
        plan = plan_rebalance([pos('WDC', 3.3539, 368.12)], 4750, [{'ticker': 'MU', 'target_pct': 20}],
                              prices={'MU': 889.15}, fractional=True)
        assert plan['manual_orders'] == []

    def test_entries_carry_routing_metadata(self):
        plan = plan_rebalance([pos('MU', 1, 800, con_id=9939)], 5000, [{'ticker': 'MU', 'target_pct': 32}])
        o = plan['orders'][0]
        assert o['con_id'] == 9939 and o['primary_exchange'] == 'NASDAQ' and o['currency'] == 'USD'
        assert o['status'] == 'preview'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
