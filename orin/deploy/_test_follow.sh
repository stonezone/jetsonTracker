#!/usr/bin/env bash
# Restart dashboard, then exercise the Vision-Follow endpoints end-to-end.
set -u
B=http://localhost:8080
sudo systemctl restart dashboard
sleep 2
echo "dashboard=$(systemctl is-active dashboard)"

curl -s --max-time 5 "$B/api/follow/status" > /tmp/_f0.json
python3 -c "import json;d=json.load(open('/tmp/_f0.json'));print('follow before: running='+str(d['running']))"

echo "start follow:"; curl -s -X POST --max-time 6 "$B/api/follow/start"; echo
sleep 5
curl -s --max-time 5 "$B/api/follow/status" > /tmp/_f1.json
python3 -c "import json;d=json.load(open('/tmp/_f1.json'));print('follow running:', d['running'], '| last:', (d.get('last') or '')[:80])"
curl -s --max-time 5 "$B/api/session" > /tmp/_s.json
python3 -c "import json;d=json.load(open('/tmp/_s.json'));print('session state:', d['state'])"

echo "stop follow (restores camera home, ~7s):"; curl -s -X POST --max-time 20 "$B/api/follow/stop"; echo
sleep 1
curl -s --max-time 5 "$B/api/follow/status" > /tmp/_f2.json
python3 -c "import json;d=json.load(open('/tmp/_f2.json'));print('follow after stop: running='+str(d['running']))"
