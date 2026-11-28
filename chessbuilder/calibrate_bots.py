"""Opt-in local benchmark. Report observations, not certified Elo."""
import argparse
import json
import chess
import chess.engine
import chess.pgn
from chessbuilder.bots import VERSION
from chessbuilder.bots import Settings,choose_candidate,score_cp,PROFILES
from chessbuilder.engine import executable,serialize,identity
from chessbuilder.db import connect,migrate
from chessbuilder.coach import wilson
from chessbuilder.training import now

def summarize(records):
    completed=[g for g in records if g["result"]!="*"]
    wins=sum(g["result"]=="win" for g in completed);draws=sum(g["result"]=="draw" for g in completed);losses=sum(g["result"]=="loss" for g in completed)
    moves=sum(g["policy_moves"] for g in records)
    return {"completed":len(completed),"incomplete":len(records)-len(completed),"wins":wins,"draws":draws,"losses":losses,
            "score":(wins+.5*draws)/len(completed) if completed else None,"win_rate_interval_95":wilson(wins,len(completed)),
            "policy_moves":moves,"capture_rate":sum(g["captures"] for g in records)/moves if moves else None,"check_rate":sum(g["checks"] for g in records)/moves if moves else None,
            "mean_candidate_cp_loss":sum(g["candidate_cp_loss"] for g in records)/moves if moves else None}

def run(profile,strength,reference_elo,games,seed,max_plies,resume=None):
    if not 2<=games<=40 or games%2 or not 20<=max_plies<=300:raise ValueError("Use even 2–40 games and 20–300 plies")
    settings=Settings(profile=profile,strength=strength,seed=seed).model_dump()
    path=executable()
    if not path:raise RuntimeError("Stockfish not configured")
    migrate()
    records=[];ref=policy=None
    config={"profile":profile,"strength":strength,"reference_elo":reference_elo,"games":games,"seed":seed,"max_plies":max_plies,"policy":VERSION,"protocol":"paired-v2"}
    try:
        policy=chess.engine.SimpleEngine.popen_uci(path,timeout=10);ref=chess.engine.SimpleEngine.popen_uci(path,timeout=10)
        policy.configure({"Threads":1,"Hash":32});ref.configure({"Threads":1,"Hash":32})
        option=ref.options.get("UCI_Elo")
        if not option or not option.min<=reference_elo<=option.max:raise ValueError("Reference Elo outside this Stockfish build's supported range")
        ref.configure({"UCI_LimitStrength":True,"UCI_Elo":reference_elo})
        engine_identity=identity(policy,path)
        def report():
            return {**summarize(records),"records":records,"engine":engine_identity,"reference":"Stockfish UCI_Elo nominal, not independent human rating","calibrated":False,"book":"disabled","status":"complete" if len(records)==games else "partial","planned":games}
        with connect() as c:
            c.execute("BEGIN IMMEDIATE")
            if resume is None:
                rid=c.execute("INSERT INTO bot_calibration(created_at,config,result) VALUES(?,?,?)",(now(),json.dumps(config),json.dumps(report()))).lastrowid
            else:
                row=c.execute("SELECT config,result FROM bot_calibration WHERE id=?",(resume,)).fetchone()
                if not row:raise ValueError("Calibration report not found")
                saved=json.loads(row["result"])
                if json.loads(row["config"])!=config or saved.get("engine")!=engine_identity:raise ValueError("Resume requires identical configuration and engine")
                records=saved["records"];rid=resume
        print(json.dumps({"report_id":rid,"saved_games":len(records),"planned":games}),flush=True)
        for i in range(len(records),games):
            b=chess.Board();color=chess.WHITE if i%2==0 else chess.BLACK;checks=captures=loss=moves=0
            policy_game=object();ref_game=object()
            for ply in range(max_plies):
                if b.is_game_over(claim_draw=True):break
                if b.turn==color:
                    nodes=int(4000+((strength-800)/1600)**2*160000)
                    scores=[serialize(x,b.turn) for x in policy.analyse(b,chess.engine.Limit(nodes=nodes),multipv=min(5,b.legal_moves.count()),game=policy_game)]
                    m=choose_candidate(b,scores,settings,seed+(i//2)*1000+ply)
                    score=next(x for x in scores if x["pv"][0]==m.uci())
                    loss+=max(0,max(score_cp(x) for x in scores)-score_cp(score));moves+=1
                    checks+=int(b.gives_check(m));captures+=int(b.is_capture(m))
                else:m=ref.play(b,chess.engine.Limit(nodes=30000),game=ref_game).move
                if m not in b.legal_moves:raise RuntimeError("Illegal motor move")
                b.push(m)
            outcome=b.outcome(claim_draw=True)
            result="*" if not outcome else "draw" if outcome.winner is None else "win" if outcome.winner==color else "loss"
            game=chess.pgn.Game.from_board(b);game.headers["Result"]=outcome.result() if outcome else "*"
            records.append({"seed":seed+i//2,"color":"white" if color else "black","result":result,"plies":len(b.move_stack),"policy_moves":moves,"captures":captures,"checks":checks,"candidate_cp_loss":loss,"pgn":str(game)})
            with connect() as c:
                c.execute("BEGIN IMMEDIATE")
                old=json.loads(c.execute("SELECT result FROM bot_calibration WHERE id=?",(rid,)).fetchone()[0])
                if len(old["records"])!=i:raise RuntimeError("Concurrent calibration resume; stop and reload")
                c.execute("UPDATE bot_calibration SET result=? WHERE id=?",(json.dumps(report()),rid))
            print(json.dumps({k:v for k,v in records[-1].items() if k!="pgn"}),flush=True)
        return {"report_id":rid,**report()}
    finally:
        for e in (ref,policy):
            if e:
                try:e.quit()
                except Exception:e.close()

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile",choices=PROFILES,default="universal");p.add_argument("--strength",type=int,default=1600)
    p.add_argument("--reference-elo",type=int,default=1600);p.add_argument("--games",type=int,default=4)
    p.add_argument("--resume",type=int);p.add_argument("--seed",type=int,default=42);p.add_argument("--max-plies",type=int,default=160)
    a=p.parse_args();print(json.dumps(run(a.profile,a.strength,a.reference_elo,a.games,a.seed,a.max_plies,a.resume),indent=2))
