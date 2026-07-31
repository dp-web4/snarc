import sqlite3
from datetime import datetime
from collections import Counter
db='/home/dp/.snarc/projects/777c4901744b/snarc.db'
c=sqlite3.connect('file:'+db+'?mode=ro',uri=True)
rows=c.execute("select surfaced_ts,cwd from retrieval_log where source='briefing' order by surfaced_ts").fetchall()
P=lambda s: datetime.strptime(s,'%Y-%m-%d %H:%M:%S')
clusters=[]; prev=None
for ts,cwd in rows:
    t=P(ts)
    if prev is None or (t-prev).total_seconds()>60: clusters.append([cwd,0])
    clusters[-1][1]+=1; prev=t
cc=Counter(c0 for c0,_ in clusters)
print(f"briefing rows {len(rows)}, span {rows[0][0]} -> {rows[-1][0]}")
print(f"briefing clusters (kimi's unit = 1 briefing = 1 session): {len(clusters)} across {len(cc)} cwd(s)\n")
for cwd,k in cc.most_common():
    print(f"  {k:4} clusters ({100*k/len(clusters):5.1f}%)  {cwd[:62] or '(empty)'}")
top=cc.most_common(1)[0][1]
print(f"\nlargest single cwd: {100*top/len(clusters):.1f}% of all units")
for m in (200,):
    for icc in (0.745,0.4,0.2):
        k=len(cc); mbar=m/k; de=1+(mbar-1)*icc
        print(f"  n={m} in {k} clusters, ICC={icc}: design effect {de:.1f} -> effective n {m/de:.0f}")
