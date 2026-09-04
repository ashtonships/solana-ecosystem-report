#!/usr/bin/env python3
"""Prepare paid-source receipts; the workflow must push ledgers before use."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path

import cadence
import dune
import xnews

LEDGERS = (
    '.github/dune-execution-ledger.json', '.github/dune-result-read-budget.json',
    '.github/x-read-budget.json',
)


def _write_receipt(path: Path, receipt: dict) -> None:
    """Persist runner-local authorization before it can be exposed to collection."""
    with path.open('x', encoding='utf-8') as output:
        json.dump(receipt, output, sort_keys=True)
        output.write('\n')
        output.flush()
        os.fsync(output.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _source_due(root: Path, source_key: str, now: datetime) -> bool:
    """Read the persisted schedule; invalid or missing cadence cannot authorize spend."""
    try:
        snapshot = json.loads((root / 'snapshots/latest.json').read_text())
    except (OSError, ValueError):
        return False
    if not isinstance(snapshot, dict):
        return False
    return cadence.source_due(snapshot.get('collection_schedule'), source_key, now)


def prepare(root: Path, output: Path, env: dict[str, str],
            now: datetime | None = None) -> dict[str, str]:
    now = now or datetime.now(timezone.utc)
    token = f"{env['GITHUB_RUN_ID']}:{env['GITHUB_RUN_ATTEMPT']}"
    settings = {
        'DUNE_EXECUTION_ENABLED': 'false', 'DUNE_PAID_READS_ENABLED': 'false',
        'X_PAID_READS_ENABLED': 'false',
    }
    output.mkdir(parents=True, exist_ok=True)
    if (env.get('DUNE_PAID_READS_ENABLED') == 'true'
            and env.get('DUNE_API_KEY_PRESENT') == 'true'):
        try:
            if not _source_due(root, 'dune', now):
                print('Dune result read not reserved: collection cadence is not due or invalid.')
            else:
                snapshot = json.loads((root / 'snapshots/latest.json').read_text())
                section = snapshot.get('dune') or {}
                due = dune.execution_refresh_due(section, now, float(env.get('DUNE_REFRESH_HOURS') or '24'))
                execution_reserved = False
                if due and env.get('DUNE_EXECUTION_ENABLED') == 'true':
                    path = root / LEDGERS[0]
                    receipt = dune.reserve_execution_attempt(path, env.get('DUNE_QUERY_ID', ''), token, now)
                    receipt_path = output / 'dune-receipt.json'
                    _write_receipt(receipt_path, receipt)
                    settings.update(DUNE_EXECUTION_ENABLED='true', DUNE_EXECUTION_LEDGER=str(path),
                                    DUNE_EXECUTION_RECEIPT=str(receipt_path))
                    execution_reserved = True
                read_path = root / LEDGERS[1]
                read_receipt = dune.reserve_result_reads(
                    read_path, env.get('DUNE_QUERY_ID', ''), token,
                    2 if execution_reserved else 1, now,
                )
                read_receipt_path = output / 'dune-result-read-receipt.json'
                _write_receipt(read_receipt_path, read_receipt)
                settings.update(
                    DUNE_PAID_READS_ENABLED='true', DUNE_RESULT_READ_LEDGER=str(read_path),
                    DUNE_RESULT_READ_RECEIPT=str(read_receipt_path),
                )
        except (OSError, ValueError, TypeError, KeyError):
            settings['DUNE_EXECUTION_ENABLED'] = 'false'
            print('Dune result read not reserved: finite accounting is missing, invalid or spent.')
    if (env.get('X_PAID_READS_ENABLED') == 'true'
            and env.get('X_BEARER_TOKEN_PRESENT') == 'true'):
        try:
            if not _source_due(root, 'news', now):
                print('X paid read not reserved: collection cadence is not due or invalid.')
            else:
                path = root / LEDGERS[2]
                receipt = xnews.reserve_post_reads(path, token, now)
                receipt_path = output / 'x-receipt.json'
                _write_receipt(receipt_path, receipt)
                settings.update(X_PAID_READS_ENABLED='true', X_READ_LEDGER=str(path),
                                X_READ_RECEIPT=str(receipt_path))
        except xnews.XSourceUnavailable as error:
            print(str(error))
        except OSError:
            print('X receipt could not be saved; reserved allowance remains spent.')
    (output / 'environment').write_text(''.join(f'{key}={value}\n' for key, value in settings.items()))
    return settings


if __name__ == '__main__':
    prepare(Path(__file__).resolve().parent, Path(os.environ['RUNNER_TEMP']) / 'paid-sources', dict(os.environ))
