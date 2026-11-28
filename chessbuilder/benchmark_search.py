"""Opt-in paired comparison on a local PGN; no application database writes."""
import argparse
import json
import statistics
import time
from pathlib import Path
import chess.engine
from chessbuilder.importer import parse
from chessbuilder.engine import Settings,LEVELS,serialize,executable,search_move,identity
from chessbuilder.review import metrics

def compare(pgn,level="quick",limit=50,multipv=3):
    if not 1<=limit<=500:raise ValueError("Use 1–500 plies")
    path=executable()
    if not path:raise RuntimeError("Stockfish not configured")
    if multipv not in (2,3,4,5) or level not in LEVELS:raise ValueError('Invalid search configuration')
    rows=[];engine_id=None
    for game,_ in parse(pgn):
        if game.errors or not game.board().is_valid() or game.board().uci_variant!="chess":continue
        b=game.board()
        for move in game.mainline_moves():
            pair={}
            for policy in ("classic","compact-v1"):
                # A fresh process per position/method avoids shared hash and cache
                # contamination. Measure search time separately from startup.
                with chess.engine.SimpleEngine.popen_uci(path,timeout=15) as e:
                    e.configure({"Threads":1,"Hash":64})
                    if "UCI_ShowWDL" in e.options:e.configure({"UCI_ShowWDL":True})
                    if engine_id is None:engine_id=identity(e,path)
                    calls=0;cache={}
                    def query(engine,board,settings,eid,root=None,multipv=1):
                        nonlocal calls
                        key=(root.uci() if root else None,multipv)
                        if key not in cache:
                            calls+=1
                            cache[key]=[serialize(x,board.turn) for x in engine.analyse(board,chess.engine.Limit(nodes=LEVELS[settings.level]),multipv=multipv,root_moves=[root] if root else None)]
                        return cache[key]
                    settings=Settings(level=level,threads=1,hash_mb=64,multipv=multipv,auto_deepen=False,search_policy=policy)
                    start=time.perf_counter()
                    result=search_move(e,b,move,settings,engine_id,search_fn=query)
                    best,played=result["best"],result["played"]
                    elapsed=time.perf_counter()-start
                    m=metrics({"best":best,"played":played})
                    pair[policy]={"seconds":elapsed,"searches":calls,"best":best,"played":played,"metric":m}
            rows.append({"fen":b.fen(),"played":move.uci(),**pair})
            b.push(move)
            if len(rows)>=limit:break
        if len(rows)>=limit:break
    paired=[r for r in rows if r["classic"]["metric"] and r["compact-v1"]["metric"]]
    differences=[abs(r["classic"]["metric"]["win_loss"]-r["compact-v1"]["metric"]["win_loss"]) for r in paired]
    cp=[abs(r["classic"]["best"]["cp"]-r["compact-v1"]["best"]["cp"]) for r in rows if r["classic"]["best"]["cp"] is not None and r["compact-v1"]["best"]["cp"] is not None]
    return {"positions":len(rows),"paired_metrics":len(paired),"level":level,"multipv":multipv,"engine":engine_id,
        "methods":{k:{"searches":sum(r[k]["searches"] for r in rows),"seconds":sum(r[k]["seconds"] for r in rows)} for k in ("classic","compact-v1")},
        "best_cp_mean_abs_difference":statistics.mean(cp) if cp else None,
        "mean_loss_pp_difference":statistics.mean(differences) if differences else None,
        "max_loss_pp_difference":max(differences) if differences else None,
        "classification_agreement":sum(r["classic"]["metric"]["classification"]==r["compact-v1"]["metric"]["classification"] for r in paired)/len(paired) if paired else None,
        "positions_detail":rows,"note":"Cold process, isolated per-position memoization, no application cache, one thread. Sample-specific comparison; not equivalence certification."}

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("pgn",type=Path);p.add_argument("--level",choices=tuple(LEVELS),default="quick");p.add_argument("--limit",type=int,default=50);p.add_argument("--multipv",type=int,choices=(2,3,4,5),default=3)
    a=p.parse_args();print(json.dumps(compare(a.pgn.read_text(encoding="utf-8-sig"),a.level,a.limit,a.multipv),indent=2))
