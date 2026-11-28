"""Conservative practice signals; no inferred human Elo or causal transfer."""
import json
from collections import defaultdict
from datetime import date
from statistics import median

MIN_NOVEL=8

def position_key(fen):
    return " ".join(fen.split()[:4])

def practice_profile(c,day):
    rows=c.execute("""SELECT a.id,a.finished_at,a.correct,a.hints,t.source,t.phase,t.themes,t.fen,t.difficulty
      FROM training_attempts a JOIN training_items t ON t.id=a.item_id
      WHERE a.finished_at IS NOT NULL AND substr(a.finished_at,1,10)<=?
      ORDER BY a.finished_at,a.id""",(day.isoformat(),)).fetchall()
    seen=set();groups=defaultdict(lambda:{"novel":0,"unassisted":0,"repeat":0,"repeat_correct":0,"ratings":[]})
    for r in rows:
        key=position_key(r["fen"]);novel=key not in seen;seen.add(key)
        success=r["correct"]==1 and r["hints"]==0
        for theme in set(json.loads(r["themes"])):
            group=groups[(theme,r["phase"],r["source"])]
            if novel:
                group["novel"]+=1;group["unassisted"]+=int(success)
                # Only external puzzle ratings from independently attempted
                # positions are comparable here. Never infer a player rating.
                if r["source"]=="lichess" and r["difficulty"] is not None:group["ratings"].append(r["difficulty"])
            else:group["repeat"]+=1;group["repeat_correct"]+=int(success)
    result=[]
    for (theme,phase,source),g in sorted(groups.items()):
        n=g["novel"];k=g["unassisted"]
        rate=k/n if n else None
        sufficient=n>=MIN_NOVEL
        target=None
        if source=="lichess" and sufficient and len(g["ratings"])>=MIN_NOVEL:
            base=median(g["ratings"])
            # Modest adjustment relative to attempted puzzle difficulty.
            target=round(max(400,min(3000,base+100 if rate>=.8 else base-100 if rate<.5 else base)))
        result.append({"theme":theme,"phase":phase,"source":source,"novel":n,"unassisted":k,"rate":rate,
                       "repeat":g["repeat"],"repeat_correct":g["repeat_correct"],"sufficient":sufficient,"target_difficulty":target})
    return result

def practice_signal(row,profiles):
    themes=json.loads(row["themes"])
    match=[x for x in profiles if x["theme"] in themes and x["phase"]==row["phase"] and x["source"]==row["source"] and x["sufficient"]]
    if not match:return 0,["Amostra de prática insuficiente"]
    weakest=min(match,key=lambda x:(x["rate"],x["theme"]))
    boost=4*(1-weakest["rate"])
    reason=[f"{weakest['theme']}: {weakest['unassisted']}/{weakest['novel']} inéditos sem dicas"]
    targets=[x["target_difficulty"] for x in match if x["target_difficulty"] is not None]
    if row["source"]=="lichess" and row["difficulty"] is not None and targets:
        target=min(targets);distance=abs(row["difficulty"]-target)
        boost+=max(-2,2-distance/200)
        reason.append(f"Dificuldade de exercício alvo: {target}")
    return boost,reason
