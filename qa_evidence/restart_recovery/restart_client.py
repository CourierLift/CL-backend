# Throwaway local-only test account; the password below is a fake value used only against a disposable local DB.
import json,sys,urllib.request as u
B="http://127.0.0.1:8099"
def call(m,p,body=None,h={}):
    r=u.Request(B+p,method=m,data=json.dumps(body).encode() if body is not None else None,headers={"Content-Type":"application/json",**h})
    try:
        with u.urlopen(r) as x: return x.status,json.loads(x.read())
    except u.HTTPError as e: return e.code,json.loads(e.read())
ORDER={"pickup_lat":30.2672,"pickup_lng":-97.7431,"dropoff_lat":30.5083,"dropoff_lng":-97.6789,"vehicle":"car","item_type":"standard","weight_lb":20,"quantity":1,"length_in":24,"width_in":18,"height_in":12}
if sys.argv[1]=="before":
    print("register",call("POST","/auth/register",{"email":"restart-qa@example.com","password":"Disposable-Pass-123!","role":"customer"})[0])
    s,t=call("POST","/auth/login",{"email":"restart-qa@example.com","password":"Disposable-Pass-123!"}); h={"Authorization":"Bearer "+t["access_token"]}
    s,q=call("POST","/quote",ORDER,h); print("quote",s,q.get("quote_id"),q.get("price"))
    s,o=call("POST","/orders",{**ORDER,"quote_id":q["quote_id"]},{**h,"Idempotency-Key":"restart-qa-key-0001"}); print("create",s,"order",o.get("id"),"price",o.get("price"))
    json.dump({"h":h,"q":q["quote_id"],"oid":o["id"],"price":o.get("price")},open("/tmp/restart_state.json","w"))
else:
    st=json.load(open("/tmp/restart_state.json")); h=st["h"]
    s,o=call("POST","/orders",{**ORDER,"quote_id":st["q"]},{**h,"Idempotency-Key":"restart-qa-key-0001"}); print("retry same key",s,"order",o.get("id"),"price",o.get("price"),"| same as original:",o.get("id")==st["oid"] and o.get("price")==st["price"])
    s,o=call("POST","/orders",{**ORDER,"quote_id":st["q"]},{**h,"Idempotency-Key":"restart-qa-key-0002"}); d=o.get("detail",{}); print("new key, same quote",s,d.get("code"),"order_id",d.get("order_id"))
