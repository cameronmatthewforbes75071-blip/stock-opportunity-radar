"""V5.8 immutable, read-only comparability recovery from *recorded* snapshot fields.
No queries to current scores, no edits to tracking ledgers or fingerprint manifests.
"""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

DATA = Path(__file__).resolve().parent / 'data'
COMPONENTS = ('growth', 'health', 'valuation', 'technical')
VALID = {'complete_four_components', 'partial_components_not_directly_comparable'}


def infer(row):
    label = str(row.get('comparability_group') or '').strip()
    if label.lower() in ('nan','none','null'):
        label = ''
    if label in VALID:
        return label, 'original_snapshot_label'
    vals = [pd.to_numeric(row.get(col), errors='coerce') for col in COMPONENTS]
    count = sum(pd.notna(v) for v in vals)
    if count == 4:
        return 'complete_four_components', 'recorded_snapshot_four_components'
    if count in (1, 2, 3):
        # Recorded research_score is required to establish scored (rather than mere partial input) status.
        if pd.notna(pd.to_numeric(row.get('research_score'), errors='coerce')):
            return 'partial_components_not_directly_comparable', 'recorded_snapshot_partial_components'
    return 'unknown_comparability', 'insufficient_original_snapshot_fields'


def recover(snapshots: pd.DataFrame):
    if not {'ticker','run_date'}.issubset(snapshots.columns):
        raise ValueError('Missing immutable snapshot keys')
    if snapshots.duplicated(['ticker','run_date']).any():
        raise ValueError('Duplicate historical snapshot keys')
    records=[]
    for row in snapshots.to_dict('records'):
        group, reason = infer(row)
        records.append({'ticker':str(row['ticker']), 'run_date':str(row['run_date']),
                        'recovered_comparability_group':group, 'classification_evidence':reason})
    output=pd.DataFrame(records,columns=['ticker','run_date','recovered_comparability_group','classification_evidence'])
    counts=output.recovered_comparability_group.value_counts().to_dict()
    evidence=output.classification_evidence.value_counts().to_dict()
    status={'run_utc':datetime.now(timezone.utc).isoformat(), 'recovery_version':'5.8',
            'total_snapshot_rows':len(output),'recovered_group_counts':{str(k):int(v) for k,v in counts.items()},
            'evidence_counts':{str(k):int(v) for k,v in evidence.items()},
            'historical_snapshot_rows_modified':0,
            'note':'Read-only evidence from original snapshot label/component values. Unknown remains unknown. No current scoring data, retroactive fundamentals, or amended audit manifest.'}
    return output,status


def main():
    data=DATA
    audit=json.loads((data/'prospective_audit_status.json').read_text())
    if audit.get('integrity_status')!='PASS':
        raise ValueError('Integrity audit must PASS before classification recovery')
    snaps=pd.read_csv(data/'prospective_score_snapshots.csv',dtype=str,keep_default_na=False)
    output,status=recover(snaps)
    output.to_csv(data/'prospective_comparability_recovery.csv',index=False)
    (data/'prospective_comparability_status.json').write_text(json.dumps(status,indent=2)+'\n')
    print(json.dumps(status,indent=2))

if __name__=='__main__': main()
