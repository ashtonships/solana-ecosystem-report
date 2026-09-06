# Mobile design and chart interaction verification

The Data introduction now presents a compact contents list with five real section destinations. Coverage disclosures use the same quiet ruled treatment. On mobile, History snapshot selectors and their chart share one padded comparison component.

All seven validator and five growth cards now provide an accessible “About this metric” disclosure, recorded readings, and direct tap/keyboard inspection of supported marks. Definitions describe the actual population and evidence; unavailable readings remain unavailable.

Inline chart gestures have distinct purposes: swipe navigates the carousel, tap inspects a reading, and “Expand chart” opens a focused dialog where dragging inspects samples. Closing, resizing, or changing routes restores the original card and visible focus. Mouse hover and keyboard inspection remain available. The shared footer wraps its timestamp before it can overlap the report name on narrow screens.

## Verification

- Offline suite: 1,108 tests passed before the final CSS-only footer correction.
- Existing interaction regression script: nine groups passed.
- `scripts/verify_mobile_design.cjs`: six interaction groups, native Chromium touch-event dispatch, 30 full-page captures, and page overflow/footer overlap checks.
- Independent read-only review found no concrete release blockers; five metric-inspector tests passed independently.
- These are browser emulation checks, not native iPhone Safari proof.

The capture matrix covers all five routes in light mode at 320, 393, 768, and 1440 pixels and dark mode at 393 and 1440 pixels. Initial viewport height is 900 pixels; every route screenshot below captures the entire document. See [machine-readable results](verification.json).

## Full-page captures

| Page | 320 light | 393 light | 768 light | 1440 light | 393 dark | 1440 dark |
|---|---|---|---|---|---|---|
| Overview | [320 light](overview-320-light.png) | [393 light](overview-393-light.png) | [768 light](overview-768-light.png) | [1440 light](overview-1440-light.png) | [393 dark](overview-393-dark.png) | [1440 dark](overview-1440-dark.png) |
| Data | [320 light](data-320-light.png) | [393 light](data-393-light.png) | [768 light](data-768-light.png) | [1440 light](data-1440-light.png) | [393 dark](data-393-dark.png) | [1440 dark](data-1440-dark.png) |
| Methods | [320 light](methods-320-light.png) | [393 light](methods-393-light.png) | [768 light](methods-768-light.png) | [1440 light](methods-1440-light.png) | [393 dark](methods-393-dark.png) | [1440 dark](methods-1440-dark.png) |
| History | [320 light](history-320-light.png) | [393 light](history-393-light.png) | [768 light](history-768-light.png) | [1440 light](history-1440-light.png) | [393 dark](history-393-dark.png) | [1440 dark](history-1440-dark.png) |
| Project | [320 light](project-320-light.png) | [393 light](project-393-light.png) | [768 light](project-768-light.png) | [1440 light](project-1440-light.png) | [393 dark](project-393-dark.png) | [1440 dark](project-1440-dark.png) |

## Interaction states

- [Validator explanation](validator-explanation-mobile.png): full-page capture with the metric disclosure open.
- [Expanded chart](expanded-chart-mobile.png): viewport capture of the modal state. A full-page screenshot resizes the browser internally and intentionally closes this dialog through its resize recovery handler; regular page captures above remain full-page.

No collector, source entitlement, billing, or economic-publication behavior changed.
