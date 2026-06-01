#!/usr/bin/env bash
# Verify the session banner state machine: idle -> recording -> idle.
set -u
B=http://localhost:8080
echo "start recording:"; curl -s -X POST --max-time 5 "$B/api/media/record/start"; echo
sleep 2
curl -s --max-time 5 "$B/api/session" > /tmp/_sess1.json
echo "session while recording:"; python3 -c "import json;d=json.load(open('/tmp/_sess1.json'));print('  state='+d['state'],'rec='+str(d['components']['recording']))"
echo "stop recording:"; curl -s -X POST --max-time 5 "$B/api/media/record/stop"; echo
sleep 1
curl -s --max-time 5 "$B/api/session" > /tmp/_sess2.json
echo "session after stop:"; python3 -c "import json;d=json.load(open('/tmp/_sess2.json'));print('  state='+d['state'])"
