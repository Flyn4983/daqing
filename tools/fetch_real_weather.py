"""采集真实 ERA5 再分析气象（Open-Meteo archive API）并打包为离线 JSON。

数据源：Open-Meteo Historical Weather API（ECMWF ERA5 再分析，0.25° 网格）
  https://archive-api.open-meteo.com/v1/archive
无需 key；本项目原型阶段打包本地离线使用（用户授权忽略版权）。

输出：
  src/brigsim/agent/web/data/real_weather.json   小时级真实气象 + 映射后的本项目天气枚举
  scenarios/REAL_WEATHER_META.json                采集元数据

WMO code → 本项目 weather 枚举 + visibility_factor 映射（与 combat/weather.py 对齐）：
  0-2 clear, 3 overcast, 45/48 fog, 51-67 rain, 71-77 snow, 80-82 rain, 85-86 snow, 95+ heavy_rain
能见度因子取 weather 经验值，并叠加降水强度微调。

核实日期：2026-11-20
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAT, LON = 37.00, 113.50           # 与真实 DEM 同区域（太行山中段）
START, END = "2024-07-10", "2024-07-12"
API = ("https://archive-api.open-meteo.com/v1/archive"
       f"?latitude={LAT}&longitude={LON}&start_date={START}&end_date={END}"
       "&hourly=temperature_2m,precipitation,wind_speed_10m,wind_direction_10m,"
       "weather_code,relative_humidity_2m"
       "&daily=precipitation_sum,wind_speed_10m_max,weather_code"
       "&timezone=Asia%2FShanghai")

# WMO code -> (weather 枚举, 基础能见度因子)
WMO_MAP = {
    0: ("clear", 1.00), 1: ("clear", 0.95), 2: ("overcast", 0.90), 3: ("overcast", 0.85),
    45: ("fog", 0.20), 48: ("heavy_fog", 0.10),
    51: ("rain", 0.70), 53: ("rain", 0.65), 55: ("rain", 0.60),
    56: ("rain", 0.60), 57: ("rain", 0.55),
    61: ("rain", 0.65), 63: ("rain", 0.55), 65: ("rain", 0.45),
    66: ("rain", 0.50), 67: ("rain", 0.45),
    71: ("snow", 0.60), 73: ("snow", 0.55), 75: ("snow", 0.50), 77: ("snow", 0.55),
    80: ("rain", 0.70), 81: ("rain", 0.55), 82: ("heavy_rain", 0.40),
    85: ("snow", 0.60), 86: ("snow", 0.50),
    95: ("heavy_rain", 0.40), 96: ("heavy_rain", 0.35), 99: ("heavy_rain", 0.30),
}


def map_code(code: int, precip: float) -> tuple[str, float]:
    weather, vis = WMO_MAP.get(int(code), ("clear", 1.00))
    # 降水越强，能见度进一步下降
    if precip >= 5.0:
        vis *= 0.75
    elif precip >= 2.0:
        vis *= 0.88
    return weather, round(max(0.06, min(1.0, vis)), 3)


def main() -> int:
    print(f"[WX] fetching ERA5 lat={LAT} lon={LON} {START}~{END}")
    req = urllib.request.Request(API, headers={"User-Agent": "brigsim-prototype/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = json.loads(resp.read().decode("utf-8"))

    h = raw["hourly"]
    rows = []
    for i, t in enumerate(h["time"]):
        code = h["weather_code"][i]
        precip = h["precipitation"][i]
        wx, vis = map_code(code, precip)
        rows.append({
            "time": t,
            "temperature_c": h["temperature_2m"][i],
            "precipitation_mm": precip,
            "wind_speed_kmh": h["wind_speed_10m"][i],
            "wind_dir_deg": h["wind_direction_10m"][i],
            "humidity_pct": h["relative_humidity_2m"][i],
            "wmo_code": code,
            "weather": wx,
            "visibility_factor": vis,
        })

    # 逐日聚合：取当日最多的天气枚举作为该日天气，最小能见度作为保守因子
    daily_rows = []
    d = raw["daily"]
    for dj, day in enumerate(d["time"]):
        day_rows = [r for r in rows if r["time"].startswith(day)]
        from collections import Counter
        wx_top = Counter(r["weather"] for r in day_rows).most_common(1)[0][0]
        daily_rows.append({
            "date": day,
            "weather": wx_top,
            "visibility_factor": min(r["visibility_factor"] for r in day_rows),
            "precip_sum_mm": d["precipitation_sum"][dj],
            "wind_max_kmh": d["wind_speed_10m_max"][dj],
            "wmo_code": d["weather_code"][dj],
        })

    payload = {
        "schema": "brigsim-real-weather/1",
        "source": "Open-Meteo Historical Weather API (ECMWF ERA5 reanalysis)",
        "region": "太行山中段（山西-河北交界）",
        "lat": LAT, "lon": LON,
        "elevation_m": raw.get("elevation"),
        "period": [START, END],
        "mapping": "WMO code -> combat/weather.py 枚举 + visibility_factor",
        "hourly": rows,
        "daily": daily_rows,
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }
    out = ROOT / "src" / "brigsim" / "agent" / "web" / "data" / "real_weather.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[WX] wrote {out}  hourly={len(rows)} daily={len(daily_rows)}")

    meta = {
        "source": payload["source"], "period": payload["period"],
        "lat": LAT, "lon": LON, "n_hours": len(rows),
        "daily_weather": [(r["date"], r["weather"], r["visibility_factor"]) for r in daily_rows],
        "license_note": "ERA5 再分析数据由 Open-Meteo 提供；原型阶段按用户授权忽略版权",
    }
    (ROOT / "scenarios" / "REAL_WEATHER_META.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[WX] daily:", [(r["date"], r["weather"], r["visibility_factor"]) for r in daily_rows])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
