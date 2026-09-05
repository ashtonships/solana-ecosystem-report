# App-wide interaction review — September 5, 2026

This pass compared original requests and reported outcomes from 30 Solana work
sessions, then reconciled the findings with public main (`991a033`). Older working
copies had already-fixed defects; the current public renderer was the baseline.

## Delivered improvements

| Area | Problem | Verified behavior |
| --- | --- | --- |
| Overview | Chart keys bubbled into carousel navigation | Chart End keeps carousel still; track End still advances |
| Overview | Desktop styles made both TPS lines violet | Non-vote line retains contrasting ink and dashed styling |
| Data | Source-filter results collapsed each other's groups | All matching groups remain open; empty search and reset work |
| History | Picker Tab dismissed options and blocked the archive footer | Selected option receives focus; Tab reaches archive link; Escape restores focus |
| History | Keyboard reached only sparse markers; pointer math assumed full SVG width | Arrow/Home/End inspect all recorded samples; SVG transforms map pointer positions |
| History | Layout changes selected a different A/B pair | Pair survives desktop/mobile transitions in both directions |
| Project | Full activity list dominated page; layouts kept separate filter state | Initial 12 records, Show more in batches, accurate count, shared filters/view/batch |
| Methods | Closed schedule retained a 296px panel; return line crossed text | Compact disclosure grows when opened; label clears the return path |

At the captured snapshot, initial Project page height changed from 11,969px to
7,021px at 393px, and 9,878px to 5,991px at 1440px. All activity remains in static
HTML, including with JavaScript disabled. Methods desktop height changed from
1,629px to 1,395px. The stored evidence is tied to this snapshot; current collection
can change content and page heights.

## Verification

- `python3 -B -m unittest tests.test_render tests.test_project_progressive -q`: 348 passed.
- `python3 -B -m unittest discover -s tests -q`: 1,099 passed.
- `scripts/verify_ui_interactions.cjs`: nine groups of actual browser checks passed;
  see [machine-readable results](verification.json).
- All five routes, Back navigation, both downloads, loading/empty/error test states,
  no-JavaScript activity fallback, filters, batching and responsive state exercised.
- Thirty complete-page captures, with no document horizontal overflow: light at
  320/393/768/1440px; dark at 393/1440px; viewport height 900px.
- Independent source review found no blocking regression. Mechanical design scan
  reported inherited style/token warnings; it is not a visual or accessibility pass.

Reproduce browser checks with an existing Playwright installation (no new package
is required by the production report):

```sh
NODE_PATH=/path/to/installed/node_modules node scripts/verify_ui_interactions.cjs http://localhost:3000/ /tmp/solana-ui-evidence
```

These are Chromium browser checks, including responsive layouts. They do not
establish native iOS Safari touch behavior, screen-reader usability, or WCAG
conformance. Fixed docks/tickers appear at their viewport position inside full-page
screenshots. Screenshots are local UI evidence; hosted release verification is a
separate step.

## Full-page evidence

| Route | 320 light | 393 light | 768 light | 1440 light | 393 dark | 1440 dark |
| --- | --- | --- | --- | --- | --- | --- |
| Overview | [Full page](overview-320-light.png) | [Full page](overview-393-light.png) | [Full page](overview-768-light.png) | [Full page](overview-1440-light.png) | [Full page](overview-393-dark.png) | [Full page](overview-1440-dark.png) |
| Data | [Full page](data-320-light.png) | [Full page](data-393-light.png) | [Full page](data-768-light.png) | [Full page](data-1440-light.png) | [Full page](data-393-dark.png) | [Full page](data-1440-dark.png) |
| Methods | [Full page](methods-320-light.png) | [Full page](methods-393-light.png) | [Full page](methods-768-light.png) | [Full page](methods-1440-light.png) | [Full page](methods-393-dark.png) | [Full page](methods-1440-dark.png) |
| History | [Full page](history-320-light.png) | [Full page](history-393-light.png) | [Full page](history-768-light.png) | [Full page](history-1440-light.png) | [Full page](history-393-dark.png) | [Full page](history-1440-dark.png) |
| Project | [Full page](project-320-light.png) | [Full page](project-393-light.png) | [Full page](project-768-light.png) | [Full page](project-1440-light.png) | [Full page](project-393-dark.png) | [Full page](project-1440-dark.png) |

## Remaining product work

- Desktop alternate History pairs still expose a smaller reading ledger than
  mobile. Expand using bound recorded comparisons without inventing historical
  threshold/anomaly findings.
- The long Data page would benefit from a more compact evidence-navigation pass;
  preserve table completeness and scoped source descriptions.
- Verify native Safari scrolling, ticker placement and touch scrubbing on an
  actual iPhone. Browser viewport emulation does not close that prior request.
- Continue contrast/typography review of dense dark-mode tables and intermediate
  layout source diagrams; this release does not claim a complete accessibility audit.
