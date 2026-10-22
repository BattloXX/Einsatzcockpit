"""Schwellen für interne Wetterhinweise (keine amtlichen Warnungen).

Die unabhängige Alarm-Engine in weather_alert_service verwendet für Sturm
17/25 m/s (Vorwarnung/akut). Diese abweichenden Schwellen bleiben unverändert.
"""

BF8 = 17.2    # m/s — Beaufort 8: storm begins, twigs break
BF10 = 24.5   # m/s — Beaufort 10: whole gale, trees uprooted

WILDFIRE_TEMP = 30.0   # °C threshold
WILDFIRE_HUM_WARN = 30.0   # % — warn level
WILDFIRE_HUM_DANGER = 20.0  # % — danger level
WILDFIRE_NO_RAIN = 0.5  # mm — nowcast total below this = "no rain"

# Starkregen / Hochwasser — Schwellwerte
RAIN_NOWCAST_WARN = 15.0    # mm Summe im ~3h-Nowcast
RAIN_NOWCAST_DANGER = 30.0
RAIN_FC6_WARN = 25.0        # mm akkumuliert bis +6h
RAIN_FC6_DANGER = 40.0

# Schneefall / Schneelast — Schwellwerte (Niederschlag als Schnee bei Temp ≤ Grenze)
SNOW_TEMP_MAX = 1.0         # °C — darunter fällt Niederschlag als Schnee
SNOW_NOWCAST_WARN = 3.0     # mm Wasseräquivalent / ~3h  (≈ 3 cm Neuschnee)
SNOW_NOWCAST_DANGER = 8.0   # mm  (≈ 8–10 cm Neuschnee)
SNOW_FC6_WARN = 8.0         # mm akkumuliert bis +6h
SNOW_FC6_DANGER = 15.0

# Glatteis / gefrierender Regen — Temperaturfenster
ICE_TEMP_LOW = -1.0
ICE_TEMP_HIGH = 1.0
ICE_MIN_RAIN = 0.2          # mm Niederschlag im Nowcast nötig

