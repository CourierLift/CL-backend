import os, json
os.environ.update(CL_APP_ENV="test", CL_SECRET_KEY="test-only-secret-not-for-production", CL_DATABASE_URL="sqlite:////tmp/clex.db", CL_DEVELOPMENT_FALLBACK_MILES="7.5")
from fastapi.testclient import TestClient
from backend.main import app
from backend.database import SessionLocal
from backend.models import Quote
from datetime import datetime, timedelta, timezone
P={"origin":"100 Main Street","destination":"200 Oak Avenue","vehicle":"car","item_type":"standard","weight_kg":8,"quantity":2,"length_in":24,"width_in":18,"height_in":14,"delivery_requirements":[],"surge":3.0}
def show(label, r):
    b=r.json()
    if isinstance(b,dict) and "pricing_snapshot" in b: b["pricing_snapshot"]="{...copied from quote...}"
    print(f"### {label}\n{r.request.method} {r.request.url.path} -> {r.status_code}\n{json.dumps(b,indent=1,default=str)}\n")
with TestClient(app) as c:
    tok={}
    for e,role in [("cust-a@example.com","customer"),("cust-b@example.com","customer")]:
        c.post("/auth/register",json={"email":e,"password":"secure-pass","role":role})
        tok[e]={"Authorization":"Bearer "+c.post("/auth/login",json={"email":e,"password":"secure-pass"}).json()["access_token"]}
    A,B=tok["cust-a@example.com"],tok["cust-b@example.com"]
    show("anonymous estimate (client surge 3.0 ignored)", c.post("/quote/estimate",json=P))
    q=c.post("/quote/estimate",json=P,headers=A); show("authenticated estimate",q); qid=q.json()["quote_id"]
    body={**P,"quote_id":qid}
    show("create", c.post("/orders/create_compat",json=body,headers={**A,"Idempotency-Key":"ex-attempt-0001"}))
    show("replay same key", c.post("/orders/create_compat",json=body,headers={**A,"Idempotency-Key":"ex-attempt-0001"}))
    show("same quote different key", c.post("/orders/create_compat",json=body,headers={**A,"Idempotency-Key":"ex-attempt-0002"}))
    show("other customer uses quote", c.post("/orders/create_compat",json=body,headers={**B,"Idempotency-Key":"ex-attempt-0003"}))
    show("missing key", c.post("/orders/create_compat",json=body,headers=A))
    show("missing quote_id", c.post("/orders/create_compat",json=P,headers={**A,"Idempotency-Key":"ex-attempt-0004"}))
    q2=c.post("/quote/estimate",json=P,headers=A).json()["quote_id"]
    show("details changed", c.post("/orders/create_compat",json={**P,"weight_kg":50,"quote_id":q2},headers={**A,"Idempotency-Key":"ex-attempt-0005"}))
    show("same key different request", c.post("/orders/create_compat",json={**P,"quote_id":q2},headers={**A,"Idempotency-Key":"ex-attempt-0001"}))
    with SessionLocal() as s:
        s.get(Quote,q2).expires_at=datetime.now(timezone.utc)-timedelta(seconds=1); s.commit()
    show("expired quote", c.post("/orders/create_compat",json={**P,"quote_id":q2},headers={**A,"Idempotency-Key":"ex-attempt-0006"}))
    show("malformed body", c.post("/orders/create_compat",json={"origin":"","quantity":"x"},headers={**A,"Idempotency-Key":"ex-attempt-0007"}))
    show("no auth", c.post("/orders/create_compat",json=body))
    C={"pickup_lat":30.2672,"pickup_lng":-97.7431,"dropoff_lat":30.5083,"dropoff_lng":-97.6789,"weight_lb":20}
    cq=c.post("/quote",json=C,headers=A); show("coordinate quote",cq)
    show("coordinate create", c.post("/orders",json={**C,"quote_id":cq.json()["quote_id"]},headers={**A,"Idempotency-Key":"ex-coord-0001"}))
    r=c.options("/orders/create_compat",headers={"Origin":"http://localhost:5173","Access-Control-Request-Method":"POST","Access-Control-Request-Headers":"authorization,content-type,idempotency-key"})
    print("### CORS preflight ->",r.status_code,dict((k,v) for k,v in r.headers.items() if k.startswith("access-control")))
