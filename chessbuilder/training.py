"""Local exercise generation, curated puzzle ingestion and attempt state."""
import csv
import hashlib
import io
import json
import threading
from datetime import datetime,timezone
import chess
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,Field
from chessbuilder.db import connect
from chessbuilder.review import select_job,get_analyses,metrics,win_percent,filters
from chessbuilder.tactics import diagnose,phase,legal_line

router=APIRouter(prefix="/api/training")
VERSION="exercise-v1"
ADJUDICATION_LOCK=threading.Lock()
def now():return datetime.now(timezone.utc).isoformat()
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":")).encode()).hexdigest()
def reconstruct(initial,history):
    b=chess.Board(initial)
    for uci in history:b.push_uci(uci)
    return b

def equivalent(best,candidate):
    # A winning mate must remain a winning mate; a forced loss must not
    # become shorter by more than one move.
    bm,cm=best.get("mate"),candidate.get("mate")
    if bm is not None:
        if bm>0:return cm is not None and 0<cm<=bm+1
        if bm<0:return cm is None or cm>0 or cm<=bm+1
    if cm is not None:return cm>0
    if best.get("cp") is None or candidate.get("cp") is None:return False
    return best["cp"]-candidate["cp"]<=30 and win_percent(best)-win_percent(candidate)<=2

def verified_solutions(board,payload,exclude=None):
    best=payload["best"]
    settings=payload.get("settings",{})
    if settings.get("level") not in ("deep","detailed"):return []
    if (best.get("depth") or 0)<10:return []
    if not best.get("pv") or best["pv"][0]==exclude:return []
    if exclude and any(s.get("pv") and s["pv"][0]==exclude and equivalent(best,s) for s in payload.get("alternatives",[])):return []
    result={}
    for score in [best,*payload.get("alternatives",[])]:
        pv=score.get("pv") or []
        if not pv or pv[0]==exclude or not legal_line(board,pv):continue
        if (score.get("depth") or 0)<10 or not equivalent(best,score):continue
        if pv[0] not in result:result[pv[0]]=pv
    return list(result.values())

def insert_item(c,source,game_id,ply,job_id,board,kind,themes,solutions,evidence,difficulty,key=None):
    history=[m.uci() for m in board.move_stack]
    initial=board.root().fen(en_passant="fen")
    key=key or digest([VERSION,source,initial,history,board.fen(),kind,solutions])
    c.execute("""INSERT OR IGNORE INTO training_items(item_key,source,game_id,ply,job_id,initial_fen,history,fen,kind,themes,phase,solution,evidence,difficulty,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(key,source,game_id,ply,job_id,initial,json.dumps(history),board.fen(),kind,json.dumps(sorted(set(themes))),phase(board),json.dumps(solutions),json.dumps(evidence),difficulty,now()))
    return c.execute("SELECT changes()").fetchone()[0]

class Generate(BaseModel):
    speed:str|None=None
    start:str|None=None
    end:str|None=None
    limit:int=Field(default=100,ge=1,le=1000)

@router.post("/generate")
def generate(body:Generate):
    added=eligible=unstable=0
    where,args=filters(speed=body.speed,start=body.start,end=body.end)
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        games=c.execute("SELECT g.* FROM games g"+where+(" AND " if where else " WHERE ")+"g.player_color IS NOT NULL ORDER BY g.played_at DESC,g.id DESC LIMIT ?",(*args,body.limit)).fetchall()
        for g in games:
            job=select_job(c,g["id"])
            if not job:continue
            # Retire superseded items even when the new evaluation no longer identifies an error.
            c.execute("UPDATE training_items SET active=0 WHERE source='personal' AND game_id=?",(g["id"],))
            analyses=get_analyses(c,job)
            b=chess.Board(g["initial_fen"])
            for row in c.execute("SELECT ply,uci FROM moves WHERE game_id=? ORDER BY ply",(g["id"],)).fetchall():
                m=chess.Move.from_uci(row["uci"])
                a=analyses.get(row["ply"])
                if a and a["perspective"]==g["player_color"]:
                    met=metrics(a)
                    if met and met["win_loss"]>=5:
                        eligible+=1
                        solutions=verified_solutions(b,a,exclude=m.uci()) if a.get("played",{}).get("pv",[None])[0:1]==[m.uci()] else []
                        if not solutions:unstable+=1
                        else:
                            d=diagnose(b,m,a)
                            themes=[f["theme"] for f in d["findings"] if f["confidence"]=="line_witness" and f["kind"] in ("missed","allowed")]
                            kind="mate" if (a["best"].get("mate") or 0)>0 else "defense" if met["before"]<55 else "conversion" if met["before"]>=75 else "best_move"
                            evidence={"diagnosis":d,"loss_pp":met["win_loss"],"played":m.uci(),"reference":a["best"],"analysis_digest":digest(a),"game_id":g["id"],"ply":row["ply"],"mode":"first"}
                            def add_exercise(position,exercise_kind,lines,labels,details):
                                nonlocal added
                                initial=position.root().fen(en_passant="fen")
                                history=json.dumps([x.uci() for x in position.move_stack])
                                duplicate=c.execute("SELECT 1 FROM training_items WHERE active=1 AND source='personal' AND initial_fen=? AND history=? AND kind=? AND game_id<>?",(initial,history,exercise_kind,g["id"])).fetchone()
                                if duplicate:return
                                key=digest([VERSION,g["id"],row["ply"],digest(a),exercise_kind])
                                added+=insert_item(c,"personal",g["id"],row["ply"],job["id"],position,exercise_kind,labels or ["unclassified"],lines,details,None,key)
                                c.execute("UPDATE training_items SET active=1 WHERE item_key=?",(key,))
                            add_exercise(b,kind,solutions,themes,evidence)
                            # Full calculation only where a supplied winning mate line
                            # actually terminates in checkmate within a bounded horizon.
                            if kind=="mate":
                                complete=[pv for pv in solutions if len(pv)%2==1 and 3<=len(pv)<=9 and legal_line(b,pv)[-1][2].is_checkmate()]
                                if complete:add_exercise(b,"calculation",complete,["mate"],{**evidence,"mode":"line","goal":"mate"})
                            allowed=[f["theme"] for f in d["findings"] if f["confidence"]=="line_witness" and f["kind"]=="allowed"]
                            badpv=a["played"].get("pv",[])
                            if allowed and len(badpv)>1 and legal_line(b,badpv):
                                after=b.copy();after.push(m)
                                # This PV is evidence of a refutation, not an independently
                                # exhaustive list of the opponent's best alternatives.
                                add_exercise(after,"refutation",[badpv[1:]],allowed,{**evidence,"played":None,"reference":None,"mode":"first"})
                b.push(m)
    return {"added":added,"eligible":eligible,"unstable":unstable}

class PuzzleImport(BaseModel):
    csv_text:str=Field(min_length=1,max_length=10_000_000)
    theme:str|None=None
    limit:int=Field(default=500,ge=1,le=5000)

@router.post("/puzzles/import")
def import_puzzles(body:PuzzleImport):
    reader=csv.DictReader(io.StringIO(body.csv_text))
    required={"PuzzleId","FEN","Moves","Rating","Themes"}
    if not required.issubset(reader.fieldnames or []):raise HTTPException(422,"CSV Lichess sem cabeçalho obrigatório")
    added=skipped=invalid=selected=0
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        for row in reader:
            if selected>=body.limit:break
            themes=(row.get("Themes") or "").split()
            if body.theme and body.theme not in themes:continue
            selected+=1
            try:
                b=chess.Board(row["FEN"]);pv=row["Moves"].split()
                if not b.is_valid() or len(pv)<2 or len(pv)%2!=0 or not legal_line(b,pv):raise ValueError("PV inválida")
                b.push_uci(pv[0]);solution=pv[1:]
                rating=int(row["Rating"])
                if not row["PuzzleId"]:raise ValueError("ID vazio")
                aliases={"discoveredAttack":"discovered_attack","defensiveMove":"defense","hangingPiece":"material","mateIn1":"mate","mateIn2":"mate","mateIn3":"mate","mateIn4":"mate","mateIn5":"mate","intermezzo":"zwischenzug","removalOfTheGuard":"remove_defender"}
                normalized=[aliases.get(t,t) for t in themes]
                n=insert_item(c,"lichess",None,None,None,b,"calculation",normalized,[solution],{"puzzle_id":row["PuzzleId"],"license":"CC0","url":"https://lichess.org/training/"+row["PuzzleId"]},rating,"lichess:"+row["PuzzleId"])
                added+=n;skipped+=1-n
            except (ValueError,TypeError,KeyError,AttributeError):invalid+=1
    return {"added":added,"skipped":skipped,"invalid":invalid}

def public_item(row):
    return {k:row[k] for k in ("id","source","game_id","ply","fen","kind","phase","difficulty","created_at")}|{"themes":json.loads(row["themes"]),"turn":"white" if chess.Board(row["fen"]).turn else "black"}

@router.get("/items")
def items(theme:str|None=None,source:str|None=None,start:str|None=None,end:str|None=None,limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0)):
    clauses=["t.active=1"];args=[]
    if theme:clauses.append("EXISTS(SELECT 1 FROM json_each(t.themes) WHERE value=?)");args.append(theme)
    if source:clauses.append("t.source=?");args.append(source)
    if start:clauses.append("substr(COALESCE(g.played_at,t.created_at),1,10)>=?");args.append(start)
    if end:clauses.append("substr(COALESCE(g.played_at,t.created_at),1,10)<=?");args.append(end)
    with connect() as c:
        rows=c.execute("SELECT t.* FROM training_items t LEFT JOIN games g ON g.id=t.game_id WHERE "+" AND ".join(clauses)+" ORDER BY t.created_at DESC,t.id DESC LIMIT ? OFFSET ?",(*args,limit,offset)).fetchall()
        return [public_item(r) for r in rows]

def load_attempt(c,aid):
    a=c.execute("SELECT * FROM training_attempts WHERE id=?",(aid,)).fetchone()
    if not a:raise HTTPException(404,"Tentativa não encontrada")
    t=c.execute("SELECT * FROM training_items WHERE id=?",(a["item_id"],)).fetchone()
    return a,t

def state(a,t,reveal=False):
    answers=json.loads(a["answers"]);solutions=json.loads(t["solution"])
    b=reconstruct(t["initial_fen"],json.loads(t["history"]))
    for u in answers:b.push_uci(u)
    data={"id":a["id"],"item":public_item(t),"fen":b.fen(),"answers":answers,"status":a["status"],"hints":a["hints"],"legal_moves":[m.uci() for m in b.legal_moves] if a["status"]=="active" else [],"correct":a["correct"],"elapsed_ms":a["elapsed_ms"]}
    if reveal or a["status"]!="active":
        data["solutions"]=solutions;data["evidence"]=json.loads(t["evidence"])
        root=reconstruct(t["initial_fen"],json.loads(t["history"]))
        data["solution_san"]=[[step[3] for step in legal_line(root,pv)] for pv in solutions]
        data["solution_positions"]=[[root.fen(),*[step[2].fen() for step in legal_line(root,pv)]] for pv in solutions]
    return data

@router.post("/items/{item_id}/start")
def start_attempt(item_id:int):
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        t=c.execute("SELECT * FROM training_items WHERE id=? AND active=1",(item_id,)).fetchone()
        if not t:raise HTTPException(404,"Exercício não encontrado")
        a=c.execute("SELECT * FROM training_attempts WHERE item_id=? AND status='active' ORDER BY id DESC LIMIT 1",(item_id,)).fetchone()
        if not a:
            aid=c.execute("INSERT INTO training_attempts(item_id,started_at) VALUES(?,?)",(item_id,now())).lastrowid
            a=c.execute("SELECT * FROM training_attempts WHERE id=?",(aid,)).fetchone()
        return state(a,t)


def judge_alternative(item,uci):
    from chessbuilder.engine import executable,serialize
    import chess.engine
    board=reconstruct(item["initial_fen"],json.loads(item["history"]))
    try:move=chess.Move.from_uci(uci)
    except ValueError:raise HTTPException(422,"Lance inválido")
    if move not in board.legal_moves:raise HTTPException(422,"Lance ilegal")
    path=executable()
    if not path:raise HTTPException(409,"Stockfish necessário para verificar esta alternativa")
    if not ADJUDICATION_LOCK.acquire(blocking=False):raise HTTPException(409,"Outra alternativa está em análise")
    engine=None
    try:
        engine=chess.engine.SimpleEngine.popen_uci(path,timeout=10)
        engine.configure({"Threads":1,"Hash":16})
        limit=chess.engine.Limit(nodes=120000,time=2)
        best=serialize(engine.analyse(board,limit),board.turn)
        played=serialize(engine.analyse(board,limit,root_moves=[move]),board.turn)
        if min(best.get("depth") or 0,played.get("depth") or 0)<10 or not legal_line(board,played.get("pv")):
            raise HTTPException(409,"Análise insuficiente; alternativa não pontuada")
        evidence=json.loads(item["evidence"])
        ref=evidence.get("reference")
        if ref is not None and not equivalent(ref,best):
            raise HTTPException(409,"A avaliação da posição mudou; regenere o exercício")
        if evidence.get("goal")=="mate" and not (best.get("mate") or 0)>0:
            raise HTTPException(409,"Mate não demonstrado nesta busca")
        if equivalent(best,played):
            if evidence.get("mode")=="line" and not legal_line(board,played["pv"])[-1][2].is_checkmate():
                raise HTTPException(409,"Linha de mate incompleta; alternativa não pontuada")
            return played["pv"]
        before,after=win_percent(best),win_percent(played)
        if before is None or after is None or before-after<5:
            raise HTTPException(409,"Alternativa inconclusiva; não pontuada")
        return []
    except (chess.engine.EngineError,OSError,TimeoutError) as exc:
        raise HTTPException(503,"Stockfish indisponível para validação") from exc
    finally:
        try:
            if engine:
                try:engine.quit()
                except Exception:engine.close()
        finally:ADJUDICATION_LOCK.release()

class Answer(BaseModel):
    uci:str=Field(min_length=4,max_length=5)
    expected_ply:int=Field(ge=0)

def finish(c,a,status,answers):
    elapsed=max(0,int((datetime.now(timezone.utc)-datetime.fromisoformat(a["started_at"])).total_seconds()*1000))
    c.execute("UPDATE training_attempts SET answers=?,status=?,correct=?,elapsed_ms=?,finished_at=? WHERE id=?",(json.dumps(answers),status,int(status=="passed"),elapsed,now(),a["id"]))

@router.post("/attempts/{aid}/answer")
def answer(aid:int,body:Answer):
    alternative=None
    with connect() as c:
        a,t=load_attempt(c,aid)
        previous=json.loads(a["answers"])
        if a["status"]!="active" or len(previous)!=body.expected_ply:raise HTTPException(409,"Tentativa ou posição mudou")
        if t["source"]=="personal":
            known={pv[len(previous)] for pv in json.loads(t["solution"]) if pv[:len(previous)]==previous and len(pv)>len(previous)}
            evidence=json.loads(t["evidence"])
            wrong=evidence.get("played") if not previous else None
            if body.uci not in known and body.uci!=wrong:
                candidate=dict(t)
                candidate["history"]=json.dumps(json.loads(t["history"])+previous)
                if previous:
                    evidence["reference"]=None
                    candidate["evidence"]=json.dumps(evidence)
                alternative=(candidate,body.uci)
    verdict=judge_alternative(*alternative) if alternative else None
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        a,t=load_attempt(c,aid)
        if a["status"]!="active":raise HTTPException(409,"Tentativa encerrada")
        answers=json.loads(a["answers"])
        if len(answers)!=body.expected_ply:raise HTTPException(409,"Posição mudou")
        b=reconstruct(t["initial_fen"],json.loads(t["history"]))
        for u in answers:b.push_uci(u)
        try:m=chess.Move.from_uci(body.uci)
        except ValueError:raise HTTPException(422,"Lance inválido")
        if m not in b.legal_moves:raise HTTPException(422,"Lance ilegal")
        candidates=[pv for pv in json.loads(t["solution"]) if pv[:len(answers)]==answers and len(pv)>len(answers) and pv[len(answers)]==body.uci]
        if not candidates and t["source"]=="personal" and (answers or body.uci!=json.loads(t["evidence"]).get("played")):
            if verdict is None:raise HTTPException(409,"Alternativa não verificada; repita a tentativa com o motor disponível")
            if verdict:
                full=answers+verdict
                candidates=[full]
                stored=json.loads(t["solution"])
                if full not in stored:
                    stored.append(full)
                    c.execute("UPDATE training_items SET solution=? WHERE id=?",(json.dumps(stored),t["id"]))
        answers.append(body.uci)
        if not candidates:finish(c,a,"failed",answers)
        elif (t["source"]=="personal" and json.loads(t["evidence"]).get("mode")!="line") or any(len(pv)==len(answers) for pv in candidates):
            finish(c,a,"passed",answers)
        else:
            # Imported Lichess puzzles contain the opponent response and next user move.
            chosen=candidates[0]
            if len(chosen)>len(answers):answers.append(chosen[len(answers)])
            if len(chosen)==len(answers):finish(c,a,"passed",answers)
            else:c.execute("UPDATE training_attempts SET answers=? WHERE id=?",(json.dumps(answers),aid))
        a,t=load_attempt(c,aid)
        return state(a,t)

@router.post("/attempts/{aid}/hint")
def hint(aid:int):
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        a,t=load_attempt(c,aid)
        if a["status"]!="active":raise HTTPException(409,"Tentativa encerrada")
        answers=json.loads(a["answers"])
        pv=next(p for p in json.loads(t["solution"]) if p[:len(answers)]==answers and len(p)>len(answers))
        m=pv[len(answers)]
        hints=a["hints"]+1
        c.execute("UPDATE training_attempts SET hints=? WHERE id=?",(hints,aid))
        return {"hint":"Peça na casa "+m[:2] if hints==1 else "Destino "+m[2:4] if hints==2 else "Lance "+m,"hints":hints}

@router.post("/attempts/{aid}/reveal")
def reveal(aid:int):
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        a,t=load_attempt(c,aid)
        if a["status"]=="active":finish(c,a,"abandoned",json.loads(a["answers"]))
        a,t=load_attempt(c,aid)
        return state(a,t)
