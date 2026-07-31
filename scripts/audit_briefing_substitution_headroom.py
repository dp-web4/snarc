import sqlite3, glob, os
# Substitution headroom: for each briefing tier, does a rank-4 candidate exist?
# If the qualifying set is <=3, slice(0,3) shows everything and suppression leaves a HOLE.
# If it is >3, suppression PROMOTES the next-ranked item -> substitution, not removal.
shards = sorted(glob.glob('/home/dp/.snarc/projects/*/snarc.db'))
tot = {'pattern':[0,0], 'identity':[0,0], 'observation':[0,0]}
print(f"{'shard':14} {'pat>=.6':>8} {'idn>=.7':>8} {'obs>=.35 of last20':>20}")
for db in shards:
    h = os.path.basename(os.path.dirname(db))
    c = sqlite3.connect('file:'+db+'?mode=ro', uri=True)
    q = lambda s: c.execute(s).fetchone()[0]
    pat = q("select count(*) from patterns where confidence>=0.6 and kind!='proposed_identity'")
    idn = q("select count(*) from identity where confidence>=0.7")
    obs = q("select count(*) from (select salience from observations order by id desc limit 20) where salience>=0.35")
    for k,v in (('pattern',pat),('identity',idn),('observation',obs)):
        tot[k][0] += 1
        if v > 3: tot[k][1] += 1
    print(f"{h:14} {pat:8} {idn:8} {obs:20}")
    c.close()
print()
for k,(n,over) in tot.items():
    print(f"{k:12} shards with >3 qualifying (rank-4 exists -> substitution): {over}/{n}")
