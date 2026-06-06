#!/bin/zsh
# Run one fixture (or a raw input JSON) through The Enforcer and print a compact summary.
source /Users/JasonXie/enforcer/ids.env
FX="$1"
if [[ "$FX" == "{"* ]]; then INPUT="$FX"; else INPUT="{\"fixture_id\":\"$FX\"}"; fi
OUT=/tmp/enf_${2:-run}.json
# retry transient "cannot connect" network blips
for attempt in 1 2 3; do
  graphn wf run "$WF_ID" --input "$INPUT" 2>/tmp/enf_err.txt > "$OUT"
  if [[ -s "$OUT" ]]; then break; fi
  sleep 3
done
python3 - "$OUT" "$FX" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(f"### fixture: {sys.argv[2]}  status: {d.get('status')}")
if d.get('status') != 'completed':
    print("ERROR:", json.dumps(d.get('error')))
    sys.exit(0)
o = (d.get('output') or {}).get('result') or d.get('output') or {}
print(f"clean={o.get('clean')}  flag_count={o.get('flag_count')}  injection_detected={o.get('injection_detected')}  manip_conf={o.get('manipulation_confidence')}")
print("summary:", o.get('summary'))
san = o.get('sanitization') or {}
print("sanitization.triggered:", san.get('triggered'), "| findings:", san.get('findings'))
for f in o.get('flags', []):
    ev = (f.get('evidence') or '')
    ev = ev[:80].replace('\n',' ')
    print(f"  [{f.get('severity'):8}] {f.get('category'):16} {f.get('title')}  :: {ev}")
PY
