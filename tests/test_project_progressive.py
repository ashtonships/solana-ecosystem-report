"""Offline coverage for progressive disclosure of recorded Project activity."""

import shutil
import subprocess
import unittest
from html.parser import HTMLParser

import render


class ActivityMarkup(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.elements = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def activity_snapshot():
    return {
        "collected_at": "2026-09-05T12:00:00Z",
        "news": {"sources": {"agave_releases": {
            "available": True,
            "items": [{
                "title": f"Release {index}",
                "published": f"2026-08-{index + 1:02d}T12:00:00Z",
                "link": f"https://example.com/releases/{index}",
            } for index in range(31)],
        }}},
    }


class TestProjectProgressive(unittest.TestCase):
    def test_all_records_survive_without_javascript(self):
        for context in ("desktop", "mobile"):
            with self.subTest(context=context):
                parsed = ActivityMarkup(render.render_development_stream(activity_snapshot(), context))
                events = [attrs for _, attrs in parsed.elements if "data-development-event" in attrs]
                self.assertEqual(len(events), 31)
                self.assertTrue(all("hidden" not in attrs for attrs in events))
                pagination = next(attrs for _, attrs in parsed.elements if "data-development-pagination" in attrs)
                self.assertIn("hidden", pagination)
                more = next(attrs for _, attrs in parsed.elements if "data-development-more" in attrs)
                self.assertEqual(more["type"], "button")
                self.assertEqual(more["aria-controls"], f"{context}-development-events")

    @unittest.skipUnless(shutil.which("node"), "Node is needed to exercise the browser controller")
    def test_batches_filters_grid_empty_state_and_keyboard_reading_order(self):
        controller = render.MOBILE_CONTROLLER.split("  const developmentStreams =", 1)[1]
        controller = "const developmentStreams =" + controller.split("  const search =", 1)[0]
        # Run the production controller against its small DOM contract. Browser
        # layout and focus scrolling are verified separately in responsive QA.
        harness = r"""
const assert = require('node:assert/strict');
let focused = null;
class Element {
  constructor(dataset = {}) { this.dataset = dataset; this.hidden = false; this.handlers = {}; this.attrs = {}; }
  addEventListener(name, fn) { this.handlers[name] = fn; }
  setAttribute(name, value) { this.attrs[name] = value; }
  focus() { focused = this; }
  querySelector() { return null; }
  fire(name) { this.handlers[name](); }
}
const createStream = (isMobile = false) => {
const events = Array.from({length: 31}, (_, i) => new Element({
  developmentKind: i < 26 ? 'release' : 'announcement',
  developmentLane: i < 26 ? 'agave' : 'news',
  developmentAgeDays: String(i * 4),
}));
const groups = Array.from({length: 7}, (_, i) => {
  const group = new Element();
  group.querySelectorAll = () => events.slice(i * 5, (i + 1) * 5);
  return group;
});
const controls = new Element(), list = new Element(), empty = new Element();
const result = new Element(), pagination = new Element(), more = new Element();
const active = new Element(), source = new Element(), windowSelect = new Element();
source.value = 'all'; source.selectedOptions = [{textContent: 'Agave'}];
windowSelect.value = 'all'; windowSelect.selectedOptions = [{textContent: 'Last 90 days'}];
const types = ['all', 'release', 'announcement', 'incident'].map(value => new Element({developmentFilter: value}));
const views = ['timeline', 'grid'].map(value => new Element({developmentView: value}));
const elements = {
  '[data-development-controls]': controls, '[data-development-events]': list,
  '[data-development-source]': source, '[data-development-window]': windowSelect,
  '.development-filter-empty': empty, '[data-development-result-count]': result,
  '[data-development-pagination]': pagination, '[data-development-more]': more,
  '[data-development-active-filters]': active,
};
const collections = {
  '[data-development-filter]': types, '[data-development-view]': views,
  '[data-development-event]': events, '[data-development-date-group]': groups,
};
const stream = {
  querySelector: selector => elements[selector],
  querySelectorAll: selector => collections[selector],
  classList: {contains: () => isMobile},
};
return {stream, events, groups, controls, list, empty, result, pagination, more, active, source, windowSelect, types, views};
};
const desktop = createStream(), mobile = createStream(true);
const {stream, events, groups, list, empty, result, pagination, more, source, windowSelect, types, views} = desktop;
const document = {querySelectorAll: () => [desktop.stream, mobile.stream]};
"""
        checks = r"""
const visible = () => events.filter(event => !event.hidden).length;
assert.equal(visible(), 12);
assert.equal(result.textContent, '12 of 31 events');
assert.equal(groups.filter(group => !group.hidden).length, 3);
assert.equal(pagination.hidden, false);
more.fire('click');
assert.equal(visible(), 24);
assert.equal(focused, events[12]);
assert.equal(focused.attrs.tabindex, '-1');
assert.equal(result.textContent, '24 of 31 events');
views[1].fire('click');
assert.equal(list.dataset.view, 'grid');
assert.equal(visible(), 24);
more.fire('click');
assert.equal(visible(), 31);
assert.equal(result.textContent, '31 events shown');
assert.equal(pagination.hidden, true);
assert.equal(focused, events[24]);
types[1].fire('click');
assert.equal(visible(), 12);
assert.equal(result.textContent, '12 of 26 events');
assert.equal(list.dataset.view, 'grid');
more.fire('click');
source.value = 'agave'; source.fire('change');
assert.equal(visible(), 12);
more.fire('click');
windowSelect.value = '90'; windowSelect.fire('change');
assert.equal(visible(), 12);
assert.equal(result.textContent, '12 of 23 events');
types[3].fire('click');
assert.equal(visible(), 0);
assert.equal(empty.hidden, false);
assert.equal(pagination.hidden, true);
assert.equal(groups.every(group => group.hidden), true);
source.value = 'all'; source.fire('change');
windowSelect.value = 'all'; windowSelect.fire('change');
types[2].fire('click');
assert.equal(visible(), 5);
assert.equal(empty.hidden, true);
assert.equal(pagination.hidden, true);
stream.classList.contains = () => true;
types[2].fire('click');
assert.equal(result.textContent, '5 events');
// Both responsive copies always represent the latest user interaction.
// Thus crossing 700px needs no state reset or fresh interaction.
types[1].fire('click');
source.value = 'agave'; source.fire('change');
windowSelect.value = '90'; windowSelect.fire('change');
views[1].fire('click');
more.fire('click');
assert.equal(mobile.source.value, 'agave');
assert.equal(mobile.windowSelect.value, '90');
assert.equal(mobile.types[1].attrs['aria-pressed'], 'true');
assert.equal(mobile.views[1].attrs['aria-pressed'], 'true');
assert.equal(mobile.list.dataset.view, 'grid');
assert.equal(mobile.events.filter(event => !event.hidden).length, 23);
assert.equal(mobile.result.textContent, '23 events');
// Continue from mobile and return to desktop: filters, batches and view agree.
mobile.windowSelect.value = 'all'; mobile.windowSelect.fire('change');
assert.equal(windowSelect.value, 'all');
assert.equal(visible(), 12);
mobile.more.fire('click');
assert.equal(visible(), 24);
assert.equal(result.textContent, '24 of 26 events');
assert.equal(focused, mobile.events[12]);
mobile.views[0].fire('click');
assert.equal(list.dataset.view, 'timeline');
assert.equal(views[0].attrs['aria-pressed'], 'true');
assert.equal(visible(), 24);
"""
        outcome = subprocess.run(
            [shutil.which("node"), "-e", harness + controller + checks],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(outcome.returncode, 0, outcome.stderr)


if __name__ == "__main__":
    unittest.main()
