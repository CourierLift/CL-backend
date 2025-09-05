# --- backend/quote_engine.py ----------------------------------------------
# Pricing & ETA logic; no external APIs.

import math

VEHICLE = {
    "bike":        {"speed": 12, "mult": 0.90, "env": -0.35},
    "cargo_bike":  {"speed": 11, "mult": 1.00, "env": -0.30},
    "e_bike":      {"speed": 14, "mult": 0.95, "env": -0.30},
    "scooter":     {"speed": 18, "mult": 0.95, "env": 0.00},
    "motorcycle":  {"speed": 28, "mult": 1.05, "env": 0.00},
    "car":         {"speed": 24, "mult": 1.00, "env": 0.00},
    "ev_compact":  {"speed": 24, "mult": 0.98, "env": -0.10},
    "ev_sedan":    {"speed": 24, "mult": 1.02, "env": -0.10},
    "suv":         {"speed": 22, "mult": 1.15, "env": 0.00},
    "ev_suv":      {"speed": 22, "mult": 1.12, "env": -0.08},
    "van":         {"speed": 21, "mult": 1.22, "env": 0.00},
    "ev_van":      {"speed": 21, "mult": 1.20, "env": -0.06},
    "truck_light": {"speed": 20, "mult": 1.35, "env": 0.00},
    "truck_box":   {"speed": 19, "mult": 1.50, "env": 0.00},
}

ITEM_MULT = {"general":1.0,"electronics":1.2,"fragile":1.25,"perishable":1.15,"oversize":1.4}
WEATHER = {"clear":1.0,"rain":1.08,"snow":1.18,"extreme":1.35}
TRAFFIC = {"low":1.0,"med":1.15,"high":1.35}
SURGE = 1.075

def _clamp(n,a,b): return max(a, min(b,n))

def _haversine_km(lat1, lon1, lat2, lon2):
    R=6371.0; to=math.pi/180.0
    dlat=(lat2-lat1)*to; dlon=(lon2-lon1)*to
    a=math.sin(dlat/2)**2+math.cos(lat1*to)*math.cos(lat2*to)*math.sin(dlon/2)**2
    return 2*R*math.atan2(math.sqrt(a), math.sqrt(1-a))

def _size_factor(L,W,H):
    base=12*8*6
    vol=max(1.0, L*W*H)
    return _clamp((vol/base)**0.35, 0.75, 2.0)

def _weight_fee(lb): return max(0.0,(max(0.0,lb)-5.0)*0.15)

def estimate_quote(
    pickup, dropoff, vehicle, item_type, quantity,
    weight_lb, length_in, width_in, height_in, weather, traffic
):
    km=_haversine_km(pickup[0],pickup[1],dropoff[0],dropoff[1])
    miles=round(km*0.621371*1.15,2)

    v=VEHICLE.get(vehicle, VEHICLE["car"])
    base=3.5+miles*(1.45*v["mult"])

    sizeF=_size_factor(length_in, width_in, height_in)
    itemM=ITEM_MULT.get(item_type,1.0)
    wtFee=_weight_fee(weight_lb)
    env=v["env"]; access=1.25
    wx=WEATHER.get(weather,1.0)*TRAFFIC.get(traffic,1.15)

    subtotal=base*sizeF*itemM*wx*SURGE+wtFee+access+env
    price=_clamp(round(subtotal,2),4.5,999.0)

    eff=(v["speed"]/wx)*0.9
    eta=max(5, math.ceil(((miles or 0)/max(3,eff))*60+5))

    tier="Saver" if price<12 else "Standard" if price<30 else "Priority" if price<80 else "Pro Load"
    return {"price":price,"eta":eta,"miles":miles,"tier":tier}
