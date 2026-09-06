# Especificación técnica — `mcp_f1_strategy`

Servidor MCP local para Proyecto 1 (CC3067 Redes, UVG). Este documento es la especificación completa del servidor y está pensado para ser usado como input directo para generar el código del proyecto.

Repositorio: `mcp_f1_strategy`
Host que lo consume: `host_mcp_redes`

---

## 1. Descripción del caso de uso

Asistente de Gen AI para el muro de ingenieros de estrategia de Scuderia Ferrari, diseñado para apoyar al ingeniero de estrategia durante una carrera de Fórmula 1. El servidor calcula la ventana óptima de parada en boxes en base a la degradación de neumáticos, el gap con los pilotos rivales y el tiempo de pit stop, y evalúa si conviene ejecutar un **undercut** (parar antes que un rival para ganarle posición con neumáticos más frescos) o un **overcut** (extender el stint más allá de lo normal para aprovechar aire limpio).

A diferencia de pedirle al LLM que estime esto directamente, el servidor MCP ejecuta un modelo real de degradación y simulación de estrategia, de modo que las recomendaciones se basan en cálculos deterministas sobre datos reales, no en suposiciones del modelo de lenguaje.

## 2. Cómo se utiliza

El ingeniero de estrategia usa un chatbot de consola (`host_mcp_redes`) conectado a un LLM, que mantiene contexto durante toda la sesión y muestra el log JSON-RPC de cada interacción MCP. `mcp_f1_strategy` expone herramientas que reciben identificadores de circuito/temporada/sesión/vuelta y devuelven cálculos de ventana de parada, comparación de estrategias, proyección de posición e historial de estrategias pasadas. El host además usa los MCP oficiales de Filesystem y Git para guardar y versionar los reportes de estrategia generados (`generate_strategy_report`).

**Nota importante de alcance:** FastF1 da acceso a sesiones ya completadas (con datos oficiales reales), no a un live timing en vivo real. El uso del proyecto simula una carrera "reproduciendo" una sesión real vuelta a vuelta (el parámetro `current_lap`/`lap` avanza como si la carrera estuviera en curso). Esto debe explicarse así en el reporte final para dejar claro el alcance real del proyecto.

---

## 3. Fuente de datos

- **Fuente única de datos reales: [FastF1](https://docs.fastf1.dev/)** (paquete Python). Da telemetría, tiempos por vuelta, compuestos de neumático y stints por sesión (FP1-3, Q, R) de temporadas reales de F1.
- Se descarta Ergast como fuente de datos de neumáticos/stints (no los expone); Ergast no se usa en este proyecto.
- **Cache**: se usa el cache nativo de FastF1 (`fastf1.Cache.enable_cache(path)`), guardado en `./cache/` dentro del repo (en `.gitignore`, no se commitea). La primera consulta a una sesión la descarga; consultas posteriores a la misma sesión se leen del cache local sin red.
- No hay base de datos propia (SQLite, etc.) ni feed en vivo. No se implementa fallback especial ante fallas de red/FastF1: el chatbot ya depende de internet para el LLM, así que esto no introduce un nuevo punto de falla.
- Identificación de pilotos: **código de 3 letras estándar de FastF1** (ej. `"LEC"`, `"VER"`, `"HAM"`) en todos los parámetros `driver`.

## 4. Modelo de degradación de neumáticos

- Modelo **paramétrico**, calibrado con datos reales de FastF1 por **compuesto + circuito**.
- Forma del modelo: `lap_time(tire_age) = base_pace_s + degradation_rate_s_per_lap * tire_age ^ k` (lineal o cuadrático — decidir `k`/forma exacta durante implementación, dejando `model_type` como metadato explícito en la salida).
- Los coeficientes se obtienen por regresión sobre los tiempos de vuelta reales de stints de esa combinación compuesto/circuito (de la sesión consultada y/o sesiones históricas disponibles vía FastF1).
- Se reporta `r_squared` como medida de confianza del ajuste, y `sample_size_laps` (cantidad de vueltas reales usadas) para transparencia.
- Este modelo es el motor que consumen `get_pit_window`, `simulate_undercut_overcut`, `compare_strategy_options` y `predict_finish_position`.

## 5. Pit loss time

- Calculado **por circuito**, de forma real cuando hay datos suficientes: comparando tiempos de vuelta con pit stop vs. sin pit stop en sesiones reales de FastF1 de ese circuito.
- **Fallback genérico** (~20-25s) cuando no hay datos reales suficientes para ese circuito.
- Expuesto también como tool independiente (`get_pit_loss_time`) para que quede documentado de dónde sale este valor en cualquier cálculo que lo use.

## 6. Historial de estrategias

- También vía FastF1 (no hay base de datos propia). `get_historical_strategies` consulta sesiones de carreras pasadas en el circuito pedido y extrae stints (compuesto, vuelta de inicio/fin) y posición final por piloto.
- El cache nativo de FastF1 hace que consultas repetidas al mismo circuito/temporada sean rápidas tras la primera descarga.

## 7. Manejo de errores y baja confianza

Regla consistente para las 9 tools:

- **Error MCP explícito** cuando falta el dato base necesario para responder (ej. piloto no corrió esa sesión, circuito/temporada sin ninguna sesión disponible en FastF1). El mensaje de error debe ser claro para que el LLM se lo explique al usuario sin inventar una respuesta.
- **Resultado con advertencia** (no error) cuando el dato existe pero el modelo tiene baja confianza (ej. `r_squared` bajo, `sample_size_laps` pequeño). En estos casos, incluir en la respuesta un campo de advertencia (ej. `confidence: "low"` o `warning: str`) en vez de fallar — el LLM debe comunicar la incertidumbre al ingeniero, no ocultarla.

---

## 8. Tools MCP (9 total)

Transporte: **stdio** (servidor local, lanzado como subproceso por `host_mcp_redes`). No usa HTTP/SSE — ese transporte queda reservado para el servidor remoto de la segunda parte del proyecto.

### 8.1 `get_race_state`
Estado crudo de la sesión en una vuelta dada: posiciones, gaps, compuesto y edad de neumático de cada piloto. Sirve como primitiva reusada internamente por otras tools, y para consultas simples directas.

```
input:  { circuit: str, season: int, session: "R"|"Q"|"FP1"|"FP2"|"FP3", lap: int }
output: {
  lap: int,
  drivers: [
    { driver: str, position: int, gap_to_leader_s: float, gap_to_ahead_s: float,
      compound: str, tire_age_laps: int }
  ]
}
```

### 8.2 `get_tire_degradation_curve`
Expone los parámetros calibrados del modelo de degradación para un compuesto+circuito. Da transparencia al modelo y sirve como material para la especificación del reporte (punto 9 del enunciado).

```
input:  { compound: "SOFT"|"MEDIUM"|"HARD"|"INTERMEDIATE"|"WET", circuit: str, season?: int }
output: {
  compound: str, circuit: str,
  base_pace_s: float,
  degradation_rate_s_per_lap: float,
  model_type: "linear"|"quadratic",
  r_squared: float,
  sample_size_laps: int,
  data_source: "fastf1_real" | "insufficient_data_fallback"
}
```

### 8.3 `get_pit_loss_time`
```
input:  { circuit: str, season?: int }
output: { circuit: str, pit_loss_time_s: float, source: "fastf1_real"|"generic_fallback" }
```

### 8.4 `get_pit_window`
Ventana óptima de parada para un piloto, con riesgo de tráfico al salir.

```
input:  { driver: str, circuit: str, season: int, session: str, current_lap: int }
output: {
  driver: str, current_lap: int,
  optimal_window: { start_lap: int, end_lap: int },
  reasoning: str,
  traffic_risk: "low"|"medium"|"high",
  traffic_risk_reason: str
}
```

### 8.5 `simulate_undercut_overcut`
Compara adelantar (undercut) o atrasar (overcut) la parada de un piloto propio respecto a un rival.

```
input:  { own_driver: str, rival_driver: str, circuit: str, season: int, session: str, current_lap: int }
output: {
  undercut: { pit_lap: int, projected_time_delta_s: float, net_position_gain: bool },
  overcut:  { pit_lap: int, projected_time_delta_s: float, net_position_gain: bool },
  recommendation: "undercut"|"overcut"|"stay_out"|"no_clear_advantage",
  reasoning: str
}
```

### 8.6 `compare_strategy_options`
Generaliza el undercut/overcut: compara N estrategias hipotéticas arbitrarias (número y vueltas de parada, compuestos) para un mismo piloto.

```
input:  {
  driver: str, circuit: str, season: int, session: str, current_lap: int,
  strategies: [ { label: str, pit_laps: [int], compounds: [str] } ]
}
output: {
  results: [ { label: str, projected_total_time_s: float, projected_finish_position?: int } ],
  best_strategy: str
}
```

### 8.7 `get_historical_strategies`
```
input:  { circuit: str, seasons?: [int], drivers?: [str] }
output: {
  circuit: str,
  races: [
    { season: int, driver: str,
      stints: [ { compound: str, start_lap: int, end_lap: int } ],
      finish_position: int }
  ]
}
```

### 8.8 `predict_finish_position`
Encadena el motor de simulación a lo largo de toda la carrera restante para una estrategia planeada.

```
input:  { driver: str, circuit: str, season: int, session: str, current_lap: int, planned_strategy: { pit_laps: [int], compounds: [str] } }
output: {
  driver: str,
  projected_finish_position: int,
  projected_total_time_s: float,
  confidence: "low"|"medium"|"high",
  key_assumptions: [str]
}
```
`key_assumptions` debe declarar explícitamente las limitaciones del modelo (ej. "asume ritmo constante de rivales", "no considera safety car/VSC", "no considera clima cambiante").

### 8.9 `generate_strategy_report`
Formatea en Markdown las decisiones tomadas durante la sesión. **No resume ni interpreta** — recibe el resumen ya curado por el LLM/host y solo da formato determinista. El resumen/interpretación de qué decisión importó ya lo hace el LLM en la conversación, y el log crudo de interacciones JSON-RPC ya está cubierto por el requisito de logging del host (`logs/mcp_interactions.jsonl`); esta tool no debe duplicar esa responsabilidad.

```
input:  {
  race_context: { circuit: str, season: int },
  decisions: [ { lap: int, tool_used: str, summary: str } ]
}
output: { markdown_report: str, filename_suggestion: str }
```
El host es responsable de pasar `markdown_report` al Filesystem MCP para guardarlo y al Git MCP para hacer commit.

---

## 9. Stack técnico

- **Lenguaje**: Python (obligatorio de facto — FastF1 es Python-only; además consistente con `host_mcp_redes`, que también es Python).
- **Gestor de entorno/paquetes**: `uv` (mismo que el host).
- **SDK MCP**: SDK oficial de Python para MCP.
- **Transporte**: stdio.
- **Librería de datos**: `fastf1`.

## 10. Estructura del proyecto

```
mcp_f1_strategy/
├── src/
│   ├── server.py              # entrypoint MCP (registro de las 9 tools, transporte stdio)
│   ├── data/
│   │   └── fastf1_client.py   # wrapper: carga de sesiones, maneja el cache de FastF1
│   ├── models/
│   │   ├── degradation.py     # ajuste de curva por compuesto/circuito
│   │   ├── pit_loss.py        # cálculo de pit loss real + fallback genérico
│   │   └── strategy_sim.py    # motor de simulación (undercut/overcut, compare, predict_finish)
│   ├── tools/
│   │   └── f1_tools.py        # definición de las 9 tools MCP; traducen entre protocolo MCP y data/models
│   └── reports/
│       └── report_builder.py  # generate_strategy_report (formateo a Markdown)
├── cache/                      # cache local de FastF1 (en .gitignore)
├── tests/
│   └── ...                     # tests del modelo de degradación/pit loss contra datos reales conocidos
├── pyproject.toml              # uv
├── .gitignore
└── README.md                   # en inglés, con instrucciones de instalación/uso (requisito del proyecto)
```

Separación de responsabilidades intencional:
- `data/`: solo trae datos crudos de FastF1, no sabe nada de MCP ni de estrategia.
- `models/`: solo matemática/simulación pura, no sabe nada de MCP ni de FastF1 directamente (recibe datos ya extraídos).
- `tools/`: única capa que conoce el protocolo MCP; traduce input/output entre el cliente y `data/`/`models/`.
- `reports/`: formateo determinista, sin lógica de negocio.

## 11. Fuera de alcance (explícito)

- No hay live timing real de una carrera en curso (limitación de FastF1, explicada en el reporte).
- No hay base de datos propia para históricos (se usa el cache de FastF1 directamente).
- No hay fallback especial ante fallas de red/FastF1 (el chatbot ya requiere internet para el LLM).
- No se implementa `predict_finish_position` considerando safety car, clima, ni comportamiento no determinista de rivales — se declara como asunción explícita (`key_assumptions`) en la salida de esa tool.
- Transporte HTTP/SSE no aplica a este servidor (queda para el servidor remoto de la segunda parte del proyecto).

## 12. Requisitos de documentación (recordatorio del enunciado)

- `README.md` en inglés: descripción del proyecto, funcionalidades implementadas, instrucciones de instalación y uso.
- Especificación de cada tool (parámetros, endpoints) — ya cubierta en la sección 8 de este documento; debe trasladarse al README o a un documento de especificación separado del repo.
- Repositorio público, independiente del host.
