"""Persisted analysis coverage, independent of mutable queue checkpoints."""

def valid_sql(a="a",m="m"):
    return f"""CASE WHEN json_valid({a}.payload) THEN
      json_extract({a}.payload,'$.perspective') IN ('white','black')
      AND (json_type({a}.payload,'$.best.cp') IN ('integer','real') OR json_type({a}.payload,'$.best.mate')='integer')
      AND (json_type({a}.payload,'$.played.cp') IN ('integer','real') OR json_type({a}.payload,'$.played.mate')='integer')
      AND json_type({a}.payload,'$.best.pv')='array'
      AND json_array_length({a}.payload,'$.best.pv')>0
      AND json_extract({a}.payload,'$.played.pv[0]')={m}.uci
      AND json_type({a}.payload,'$.alternatives')='array'
      ELSE 0 END"""

def saved_sql(j="j"):
    return f"""(SELECT COUNT(*) FROM move_analysis a JOIN moves m
      ON m.game_id={j}.game_id AND m.ply=a.ply
      WHERE a.job_id={j}.id AND ({valid_sql()}))"""

def total_sql(j="j"):
    return f"(SELECT COUNT(*) FROM moves m WHERE m.game_id={j}.game_id)"

def complete_sql(j="j"):
    return f"({total_sql(j)}>0 AND {saved_sql(j)}={total_sql(j)})"

def saved_plies(c,job_id):
    return {r[0] for r in c.execute("SELECT a.ply FROM move_analysis a JOIN jobs j ON j.id=a.job_id JOIN moves m ON m.game_id=j.game_id AND m.ply=a.ply WHERE a.job_id=? AND ("+valid_sql()+")",(job_id,))}

def first_missing(c,job_id,game_id):
    saved=saved_plies(c,job_id)
    plies=[r[0] for r in c.execute("SELECT ply FROM moves WHERE game_id=? ORDER BY ply",(game_id,))]
    return next((p for p in plies if p not in saved),len(plies)+1),len(saved),len(plies)

def reconcile_jobs(c):
    # Never delete evaluations or silently start a historic batch.
    rows=c.execute("SELECT j.id,j.game_id FROM jobs j WHERE j.status='done' AND NOT "+complete_sql()).fetchall()
    for row in rows:
        next_ply,saved,total=first_missing(c,row["id"],row["game_id"])
        error="Partida sem lances no PGN" if not total else f"Análise incompleta: {saved}/{total} lances salvos"
        c.execute("UPDATE jobs SET status=?,next_ply=?,error=? WHERE id=?",("failed" if not total else "paused",next_ply,error,row["id"]))
    return len(rows)

def saved_ply(c,job_id,ply):
    return c.execute("SELECT 1 FROM move_analysis a JOIN jobs j ON j.id=a.job_id JOIN moves m ON m.game_id=j.game_id AND m.ply=a.ply WHERE a.job_id=? AND a.ply=? AND ("+valid_sql()+")",(job_id,ply)).fetchone() is not None
