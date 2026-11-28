import hashlib
import io
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urlparse, urljoin

import chess
import chess.pgn
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from chessbuilder.db import connect

router=APIRouter(prefix="/api")
HEADERS={"User-Agent":"ChessBuilder/0.1 (local chess analysis)"}
def iso(timestamp):
    return datetime.fromtimestamp(timestamp,tz=timezone.utc).isoformat() if timestamp is not None else None

def game_date(headers):
    value=headers.get("UTCDate",headers.get("Date",""))
    try:
        return datetime.strptime(value+" "+headers.get("UTCTime","00:00:00"),"%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None

def integer(value):
    try:
        return int(value)
    except (ValueError,TypeError):
        return None

def speed(control):
    m=re.fullmatch(r"(\d+)(?:\+(\d+))?",control or "")
    if not m:
        return None
    seconds=int(m[1])+40*int(m[2] or 0)
    return "bullet" if seconds<180 else "blitz" if seconds<600 else "rapid" if seconds<3600 else "classical"

def context_key(board):
    raw=board.root().fen(en_passant="fen")+"|"+" ".join(m.uci() for m in board.move_stack)
    return hashlib.sha256(raw.encode()).hexdigest()

def parse(text):
    stream=io.StringIO(text)
    while True:
        offset=stream.tell()
        game=chess.pgn.read_game(stream)
        if game is None:
            break
        raw=text[offset:stream.tell()].strip()
        yield game,raw

def opening_name(value):
    if not value:
        return None
    parsed=urlparse(value)
    if parsed.hostname in ("www.chess.com","chess.com") and parsed.path.startswith("/openings/"):
        return unquote(parsed.path.split("/openings/",1)[1]).replace("-"," ")
    return value

def store(game,raw,source="pgn",external_id=None,account=None,username=None,meta=None):
    if game.errors:
        raise ValueError("PGN inválido: "+str(game.errors[0]))
    if game.headers.get("Variant","Standard") not in ("Standard","Chess"):
        raise ValueError("Variante não suportada")
    meta=meta or {}
    h=game.headers
    board=game.board()
    if not board.is_valid():
        raise ValueError("Posição inicial inválida")
    initial=board.fen()
    white,black=h.get("White","?"),h.get("Black","?")
    color=next((c for c,n in (("white",white),("black",black)) if username and n.casefold()==username.casefold()),None)
    control=h.get("TimeControl")
    external_id=external_id or hashlib.sha256(raw.encode()).hexdigest()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing=conn.execute("SELECT id,player_color,account_id FROM games WHERE source=? AND external_id=?",(source,external_id)).fetchone()
        if existing:
            if existing["player_color"] is None and color is not None:
                conn.execute("UPDATE games SET player_color=?,account_id=COALESCE(account_id,?) WHERE id=?",(color,account,existing["id"]))
            return False
        cur=conn.execute("""INSERT INTO games(source,external_id,account_id,played_at,speed,white,black,white_rating,black_rating,result,player_color,opening,eco,termination,initial_fen,pgn,time_control) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (source,external_id,account,meta.get("played_at") or game_date(h),meta.get("speed") or speed(control),white,black,integer(h.get("WhiteElo")),integer(h.get("BlackElo")),h.get("Result","*"),color,h.get("Opening") or opening_name(meta.get("opening") or h.get("ECOUrl")),h.get("ECO"),meta.get("termination") or h.get("Termination"),initial,raw,control))
        game_id=cur.lastrowid
        for ply,node in enumerate(game.mainline(),1):
            fen=board.fen()
            key=context_key(board)
            conn.execute("INSERT OR IGNORE INTO positions(fen,history_key) VALUES(?,?)",(fen,key))
            pos=conn.execute("SELECT id FROM positions WHERE history_key=?",(key,)).fetchone()[0]
            san=board.san(node.move)
            board.push(node.move)
            conn.execute("INSERT INTO moves VALUES(?,?,?,?,?,?,?)",(game_id,ply,pos,san,node.move.uci(),board.fen(),node.clock()))
    return True

def account_id(source,username):
    with connect() as c:
        c.execute("INSERT OR IGNORE INTO accounts(source,username) VALUES(?,?)",(source,username))
        row=c.execute("SELECT * FROM accounts WHERE source=? AND username=?",(source,username)).fetchone()
        return dict(row)

def retry_delay(headers,attempt):
    delay=headers.get("Retry-After","")
    return min(60,max(1,int(delay))) if delay.isdigit() else 2**attempt

def request(client,url,**kw):
    # Limit redirects to the official HTTPS API; never forward to arbitrary hosts.
    redirects=0
    for i in range(4):
        try:
            while True:
                res=client.get(url,follow_redirects=False,**kw)
                if res.status_code not in (301,302,303,307,308):
                    break
                location=res.headers.get("Location")
                if not location or redirects>=3:
                    res.raise_for_status()
                target=urljoin(str(res.url),location)
                parsed=urlparse(target)
                if parsed.scheme!="https" or parsed.hostname!="api.chess.com" or parsed.port not in (None,443) or parsed.username is not None or parsed.password is not None:
                    raise ValueError("Redirecionamento externo inválido")
                url=target
                redirects+=1
            if res.status_code not in (429,500,502,503,504):
                res.raise_for_status()
                return res
            if i==3:
                res.raise_for_status()
            time.sleep(retry_delay(res.headers,i))
        except httpx.TransportError:
            if i==3: raise
            time.sleep(2**i)
    raise RuntimeError("Falha na requisição")

def sync(source,username,client):
    if source=="chess.com":
        username=username.lower()
    account=account_id(source,username)
    added=skipped=0
    errors=[]
    def ingest(raw,external,meta):
        nonlocal added,skipped
        try:
            parsed=list(parse(raw))
            if len(parsed)!=1: raise ValueError("Esperada uma partida")
            g,p=parsed[0]
            if store(g,p,source,external,account["id"],username,meta): added+=1
            else: skipped+=1
        except (ValueError,TypeError,KeyError,AttributeError) as exc:
            errors.append({"id":external,"error":str(exc)})
    if source=="chess.com":
        urls=request(client,"https://api.chess.com/pub/player/"+quote(username,safe="")+"/games/archives").json()["archives"]
        completed=set(json.loads(account["cursor"] or "[]"))
        current=datetime.now(timezone.utc).strftime("%Y/%m")
        for url in reversed(urls):
            parsed_url=urlparse(url)
            if parsed_url.scheme!="https" or parsed_url.hostname!="api.chess.com": raise ValueError("Arquivo externo inválido")
            if url in completed and not url.endswith(current): continue
            before=len(errors)
            for item in reversed(request(client,url).json()["games"]):
                if item.get("rules") not in (None,"chess"): continue
                if not item.get("pgn") or not item.get("url"):
                    errors.append({"id":item.get("url"),"error":"Partida sem PGN ou identificador"})
                    continue
                ingest(item["pgn"],item["url"],{"played_at":iso(item.get("end_time")),"speed":item.get("time_class"),"opening":item.get("eco"),"termination":(item.get("white",{}).get("result") if item.get("white",{}).get("result")!="win" else item.get("black",{}).get("result"))})
            if len(errors)==before and not url.endswith(current): completed.add(url)
            with connect() as c: c.execute("UPDATE accounts SET cursor=? WHERE id=?",(json.dumps(sorted(completed)),account["id"]))
    else:
        params={"pgnInJson":"true","clocks":"true","opening":"true","sort":"dateDesc"}
        if account["cursor"]: params["since"]=max(0,int(account["cursor"])-86400000)
        url="https://lichess.org/api/games/user/"+quote(username,safe="")
        latest=int(account["cursor"] or 0)
        for attempt in range(4):
            try:
                with client.stream("GET",url,params=params,headers={"Accept":"application/x-ndjson"}) as response:
                    if response.status_code in (429,500,502,503,504) and attempt<3:
                        delay=retry_delay(response.headers,attempt)
                    else:
                        response.raise_for_status()
                        for line in response.iter_lines():
                            if not line.strip(): continue
                            try:
                                item=json.loads(line)
                                if item.get("variant")!="standard": continue
                                ts=item.get("createdAt")
                                ingest(item["pgn"],item["id"],{"played_at":iso(ts/1000) if ts is not None else None,"speed":item.get("speed"),"opening":(item.get("opening") or {}).get("name"),"termination":item.get("status")})
                                latest=max(latest,ts or 0)
                            except (ValueError,TypeError,KeyError,AttributeError) as exc:
                                errors.append({"error":"Registro Lichess inválido: "+str(exc)})
                        break
                time.sleep(delay)
            except httpx.TransportError:
                if attempt==3: raise
                time.sleep(2**attempt)
        if not errors:
            with connect() as c: c.execute("UPDATE accounts SET cursor=? WHERE id=?",(str(latest),account["id"]))
    return {"added":added,"skipped":skipped,"errors":errors}

class SyncRequest(BaseModel):
    source:str
    username:str=Field(min_length=1,max_length=64,pattern=r"^[A-Za-z0-9_-]+$")

class PGNRequest(BaseModel):
    pgn:str=Field(min_length=1,max_length=20_000_000)
    username:str|None=None

@router.post("/import/sync")
def sync_endpoint(body:SyncRequest):
    if body.source not in ("chess.com","lichess"):
        raise HTTPException(422,"Fonte inválida")
    try:
        with httpx.Client(headers=HEADERS,timeout=httpx.Timeout(90,connect=15)) as client:
            return sync(body.source,body.username,client)
    except (httpx.HTTPError,ValueError,KeyError) as exc:
        raise HTTPException(502,str(exc)) from exc

@router.post("/import/pgn")
def import_pgn(body:PGNRequest):
    added=skipped=0
    errors=[]
    for i,(game,raw) in enumerate(parse(body.pgn),1):
        try:
            if store(game,raw,username=body.username): added+=1
            else: skipped+=1
        except ValueError as exc:
            errors.append({"game":i,"error":str(exc)})
    if not added and not skipped and not errors: errors.append({"error":"Nenhuma partida encontrada"})
    return {"added":added,"skipped":skipped,"errors":errors}

@router.get("/accounts")
def accounts():
    with connect() as c: return [dict(r) for r in c.execute("SELECT id,source,username FROM accounts ORDER BY source,username")]

