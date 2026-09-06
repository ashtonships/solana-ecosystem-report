"""History selectors and the selected chart form one comparison component."""

from copy import deepcopy
from html.parser import HTMLParser
import json
from pathlib import Path
import unittest

import render


class Element:
    def __init__(self, tag, attrs, parent):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent

    def within(self, ancestor):
        parent = self.parent
        while parent:
            if parent is ancestor:
                return True
            parent = parent.parent
        return False


class HistoryTree(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.elements = []
        self.stack = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        element = Element(tag, attrs, self.stack[-1] if self.stack else None)
        self.elements.append(element)
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.stack.append(element)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def with_class(self, name):
        return [element for element in self.elements if name in element.attrs.get('class', '').split()]

    def with_attr(self, name):
        return [element for element in self.elements if name in element.attrs]


class HistoryComponentTests(unittest.TestCase):
    def snapshots(self, count=3):
        snapshot = json.loads((Path(__file__).resolve().parent.parent / 'fixtures/sample-snapshot.json').read_text())
        items = []
        for index in range(count):
            item = deepcopy(snapshot)
            item['collected_at'] = f'2026-08-05T0{index + 1}:00:00Z'
            items.append(item)
        return items

    def test_one_shared_shell_contains_the_controls_and_all_comparison_charts(self):
        tree = HistoryTree(render.render_mobile_history(self.snapshots(), None))
        shells = tree.with_class('mobile-history-comparison-card')
        self.assertEqual(len(shells), 1)
        shell = shells[0]
        for element in tree.with_attr('data-history-picker-trigger') + tree.with_attr('data-history-chart-panel'):
            self.assertTrue(element.within(shell))
        self.assertEqual(len(tree.with_attr('data-history-picker-trigger')), 2)
        self.assertEqual(len(tree.with_class('mobile-history-chart-card')), 3)
        for name in ('mobile-snapshot-timeline', 'mobile-history-ledger', 'mobile-history-chronology'):
            self.assertTrue(tree.with_class(name))
            self.assertTrue(all(not element.within(shell) for element in tree.with_class(name)))

    def test_each_chart_and_ledger_share_pair_identity_and_default_visibility(self):
        tree = HistoryTree(render.render_mobile_history(self.snapshots(), None))
        charts = tree.with_attr('data-history-chart-panel')
        details = tree.with_attr('data-history-panel')
        chart_states = [(node.attrs['data-history-chart-pair'], 'hidden' in node.attrs) for node in charts]
        detail_states = [(node.attrs['data-history-pair'], 'hidden' in node.attrs) for node in details]
        self.assertEqual(chart_states, [('0:1', True), ('0:2', True), ('1:2', False)])
        self.assertEqual(chart_states, detail_states)
        self.assertIn("panel.hidden = panel.dataset.historyChartPair !== pair", render.MOBILE_CONTROLLER)

    def test_empty_and_single_snapshot_states_do_not_fabricate_a_chart(self):
        for count in (0, 1):
            with self.subTest(count=count):
                tree = HistoryTree(render.render_mobile_history(self.snapshots(count), None))
                self.assertEqual(len(tree.with_class('mobile-history-comparison-card')), 1)
                self.assertFalse(tree.with_attr('data-history-chart-panel'))
                self.assertEqual(len(tree.with_class('mobile-history-empty')), 1)


if __name__ == '__main__':
    unittest.main()
